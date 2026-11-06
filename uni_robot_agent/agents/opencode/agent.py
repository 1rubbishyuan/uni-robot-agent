"""OpenCode server adapter using the existing Responses-to-Qwen gateway."""
import json
from pathlib import Path
import queue
import subprocess
from uuid import uuid4

from uni_robot_agent.agents.contracts import AgentInterrupted
from uni_robot_agent.agents.managed import ManagedAgent, finalize_records
from uni_robot_agent.agents.runtime import ModelRuntime
from uni_robot_agent.agents.tool_binding import responses_calls
from uni_robot_agent.events import AssistantText
from uni_robot_agent.provider.backends.aliyun.chat_completions import AliyunChatCompletionsAdapter
from uni_robot_agent.provider.protocols.responses import ResponsesProtocol
from .config import OpenCodeConfig
from .transport import OpenCodeTransport


class OpenCodeAgent(OpenCodeConfig, ManagedAgent):
    name = 'opencode'
    trace_format = 'opencode_responses'

    def __init__(self, *, model, binary='opencode', expected_version=None, **kwargs):
        super().__init__(model=model, binary=binary, expected_version=expected_version,
                         read_calls=responses_calls, **kwargs)
        self.transport = None
        self.session_id = None

    def model_runtime(self, profile, event_bus=None):
        if profile != 'aliyun.chat_completions':
            raise ValueError('OpenCode requires the Qwen Responses bridge')
        self.binding.bus = event_bus
        protocol = ResponsesProtocol()
        protocol.trace_client = 'opencode'
        return ModelRuntime(self.trace_format, AliyunChatCompletionsAdapter(
                            thinking=False, require_response_metadata=True),
                            protocol, self.binding, finalize_records)

    def configuration(self, endpoint, system):
        return {'$schema': 'https://opencode.ai/config.json', 'autoupdate': False,
            'share': 'disabled', 'model': 'ura/' + self.model, 'small_model': 'ura/' + self.model,
            'enabled_providers': ['ura'], 'default_agent': 'robot',
            'provider': {'ura': {'npm': '@ai-sdk/openai', 'name': 'Robot gateway', 'options': {
                'baseURL': endpoint, 'apiKey': '{env:UNI_ROBOT_AGENT_MODEL_KEY}'},
                'models': {self.model: {'name': self.model, 'tool_call': True, 'attachment': True,
                    'modalities': {'input': ['text', 'image'], 'output': ['text']},
                    'options': {'store': False}}}}},
            'agent': {'robot': {'mode': 'primary', 'prompt': system,
                'permission': {'*': 'deny', 'ura_*': 'allow', 'read': 'allow', 'bash': 'allow',
                               'edit': 'allow', 'glob': 'allow', 'grep': 'allow'},
                'tools': {'task': False, 'question': False, 'websearch': False, 'webfetch': False}},
                'title': {'disable': True}, 'summary': {'disable': True}},
            'compaction': {'auto': False, 'prune': False},
            'plugin': [str(Path(__file__).with_name('bridge.js').resolve())],
            'mcp': {'ura': {'type': 'remote', 'url': self.mcp_url, 'oauth': False,
                            'headers': {'Authorization': '{env:UNI_ROBOT_AGENT_MCP_AUTH}'}}}}

    def run(self, *, prompt, task, toolkit, executor, max_turns=0, event_bus=None,
            session_dir=None, model_endpoint=None):
        if not model_endpoint:
            raise ValueError('OpenCode requires a model gateway endpoint')
        try:
            self.begin(prompt, task, executor, event_bus, session_dir, max_turns, ('ura_',))
            password = uuid4().hex
            self.env.update(OPENCODE_SERVER_PASSWORD=password, OPENCODE_SERVER_USERNAME='opencode',
                OPENCODE_DISABLE_PROJECT_CONFIG='true', OPENCODE_DISABLE_CLAUDE_CODE='true',
                OPENCODE_DISABLE_EXTERNAL_SKILLS='true', OPENCODE_DISABLE_AUTOCOMPACT='true',
                OPENCODE_DISABLE_PRUNE='true', OPENCODE_DISABLE_MODELS_FETCH='true',
                UNI_ROBOT_AGENT_SYSTEM_PROMPT=prompt.system,
                UNI_ROBOT_AGENT_MODEL_KEY=self.api_key or 'local',
                UNI_ROBOT_AGENT_MCP_AUTH='Bearer ' + self.mcp_token)
            config = self.configuration(model_endpoint, prompt.system)
            config_path = self.directory / 'opencode.json'
            config_path.write_text(json.dumps(config, indent=2) + '\n')
            self.env['OPENCODE_CONFIG'] = str(config_path)
            self.process = subprocess.Popen([self.binary, 'serve', '--hostname', '127.0.0.1', '--port', '0'],
                cwd=self.workspace, env=self.env, start_new_session=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.transport = OpenCodeTransport(self.process, self.directory,
                password=password, timeout=self.startup_timeout_s)
            session = self.transport.request('POST', '/session', {'title': 'Robot episode'})
            self.session_id = session['id']
            self.binding.thread_id = self.session_id
            self.transport.request('POST', f'/session/{self.session_id}/prompt_async', {
                'agent': 'robot', 'model': {'providerID': 'ura', 'modelID': self.model},
                'parts': [{'type': 'text', 'text': prompt.user}]})
            self.consume()
            return self.result()
        except KeyboardInterrupt:
            self.cancel('interrupted')
            raise AgentInterrupted(self.result('Interrupted by user'))
        except Exception as error:
            self.cancel(self.stop_reason or 'agent_error')
            return self.result(str(error))

    def consume(self):
        completed_text = set()
        assistants = set()
        completed_assistant = False
        while True:
            self.check_running()
            try:
                event = self.transport.events.get(timeout=0.2)
            except queue.Empty:
                continue
            if isinstance(event, Exception):
                raise event
            props = event.get('properties', {})
            info = props.get('info', props.get('part', {}))
            if (props.get('sessionID') or info.get('sessionID')) != self.session_id:
                continue
            kind = event.get('type')
            if kind == 'session.error':
                raise RuntimeError(str(props.get('error')))
            if kind == 'message.updated' and info.get('role') == 'assistant':
                assistants.add(info['id'])
                if info.get('error'):
                    raise RuntimeError(str(info['error']))
                if info.get('time', {}).get('completed'):
                    completed_assistant = True
            if kind == 'session.status' and props.get('status', {}).get('type') == 'retry':
                raise RuntimeError('OpenCode attempted an outer retry after gateway failure')
            if (kind == 'message.part.updated' and info.get('messageID') in assistants
                    and info.get('type') == 'text' and info.get('time', {}).get('end')):
                if info['id'] not in completed_text:
                    completed_text.add(info['id'])
                    self.texts.append(info.get('text', ''))
                    if self.bus:
                        self.bus.emit(AssistantText(text=info.get('text', '')))
            if kind == 'permission.asked':
                raise RuntimeError('Unexpected interactive permission request')
            if kind == 'session.idle' and completed_assistant and self.binding.exchanges:
                return

    def close(self):
        if self.transport is not None and self.session_id and self.stopped.is_set():
            try:
                self.transport.request('POST', f'/session/{self.session_id}/abort')
            except Exception:
                pass  # The owned process is terminated below even if HTTP is gone.
        try:
            super().close()
        finally:
            if self.transport is not None:
                self.transport.close()
                self.transport = None
