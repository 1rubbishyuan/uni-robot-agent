"""Run Gemini's native agent loop over ACP and a GenerateContent/Qwen bridge."""
import json
from pathlib import Path
import subprocess

from uni_robot_agent.agents.contracts import AgentInterrupted
from uni_robot_agent.agents.managed import ManagedAgent, finalize_records
from uni_robot_agent.agents.runtime import ModelRuntime
from uni_robot_agent.events import AssistantText
from uni_robot_agent.provider.backends.aliyun.gemini_chat_completions import AliyunGeminiChatAdapter
from uni_robot_agent.provider.protocols.gemini import GeminiProtocol, tool_calls
from .config import GeminiConfig
from .manifest import BRIDGE_ID, verify_bundle
from .transport import AcpTransport


class GeminiCliAgent(GeminiConfig, ManagedAgent):
    name = 'gemini_cli'
    trace_format = 'gemini_generate_content'
    text_separator = ''

    def __init__(self, *, model, binary='gemini', expected_version=None, bridge_manifest=None, **kwargs):
        super().__init__(model=model, binary=binary, expected_version=expected_version,
                         read_calls=tool_calls, **kwargs)
        self.bridge_manifest = bridge_manifest
        self.transport = None
        self.session_id = None
        self.pending_updates = []

    def verify_runtime(self):
        if not self.bridge_manifest:
            raise ValueError('A sealed Gemini bridge manifest is required')
        manifest = json.loads(Path(self.bridge_manifest).read_text())
        verify_bundle(Path(self.binary), manifest, self.expected_version)

    def model_runtime(self, profile, event_bus=None):
        if profile != 'aliyun.gemini_chat_completions':
            raise ValueError('Gemini CLI requires the Qwen GenerateContent bridge')
        self.binding.bus = event_bus
        adapter = AliyunGeminiChatAdapter()
        adapter.api_key = self.api_key
        return ModelRuntime(self.trace_format, adapter, GeminiProtocol(self.model),
                            self.binding, finalize_records)

    def configuration(self):
        return {'security': {'auth': {'selectedType': 'gemini-api-key'},
                             'folderTrust': {'enabled': True}},
            'general': {'maxAttempts': 1, 'retryFetchErrors': False,
                        'plan': {'modelRouting': False}},
            'model': {'name': self.model, 'maxSessionTurns': -1, 'skipNextSpeakerCheck': True},
            'context': {'fileName': '.ura-no-ambient-context.md', 'includeDirectoryTree': False,
                        'loadMemoryFromIncludeDirectories': False, 'discoveryMaxDirs': 0},
            'experimental': {'enableAgents': False, 'autoMemory': False, 'contextManagement': False},
            'modelConfigs': {'customAliases': {self.model: {'extends': 'base',
                'modelConfig': {'model': self.model, 'generateContentConfig': {
                    'temperature': 0, 'maxOutputTokens': 8192}}}}},
            'tools': {'exclude': ['google_web_search', 'web_fetch', 'delegate_to_agent']},
            'telemetry': {'enabled': False},
            'mcpServers': {'ura': {'httpUrl': self.mcp_url, 'trust': True,
                'headers': {'Authorization': '${UNI_ROBOT_AGENT_MCP_AUTH}'}}}}

    def request(self, method, params, timeout=None):
        return self.transport.request(method, params, check_running=self.check_running,
                                      on_notification=self.on_notification, timeout=timeout)

    def on_notification(self, method, params):
        if method != 'session/update':
            return
        if self.session_id is None:
            self.pending_updates.append(params)
            return
        if params.get('sessionId') != self.session_id:
            raise RuntimeError('Gemini sent an update for another session')
        update = params.get('update', {})
        if update.get('sessionUpdate') == 'agent_message_chunk':
            content = update.get('content', {})
            if content.get('type') == 'text':
                self.texts.append(content['text'])
                if self.bus:
                    self.bus.emit(AssistantText(text=content['text']))

    def run(self, *, prompt, task, toolkit, executor, max_turns=0, event_bus=None,
            session_dir=None, model_endpoint=None):
        if not model_endpoint:
            raise ValueError('Gemini CLI requires a model gateway endpoint')
        try:
            self.begin(prompt, task, executor, event_bus, session_dir, max_turns, ('mcp_ura_', 'ura_'))
            home = self.directory / 'home'
            settings = home / '.gemini'
            settings.mkdir(parents=True, exist_ok=True)
            (settings / 'settings.json').write_text(json.dumps(self.configuration(), indent=2) + '\n')
            # Gemini suppresses MCP discovery in untrusted folders, including ACP.
            # Trust only the scratch workspace created for this owned session.
            trusted_folders = settings / 'trustedFolders.json'
            trusted_folders.write_text(json.dumps({str(self.workspace.resolve()): 'TRUST_FOLDER'}) + '\n')
            trusted_folders.chmod(0o600)
            self.env.update(GEMINI_CLI_HOME=str(home), GEMINI_SYSTEM_MD=str(self.directory / 'system.md'),
                GEMINI_CLI_TRUSTED_FOLDERS_PATH=str(trusted_folders),
                GOOGLE_GEMINI_BASE_URL=model_endpoint.removesuffix('/v1'), GEMINI_API_KEY='local-gateway',
                UNI_ROBOT_AGENT_MCP_URL=self.mcp_url,
                UNI_ROBOT_AGENT_MCP_AUTH='Bearer ' + self.mcp_token)
            self.process = subprocess.Popen([self.binary, '--experimental-acp', '--model', self.model,
                '--approval-mode', 'yolo', '--include-directories', str(self.directory.parent)],
                cwd=self.workspace, env=self.env, start_new_session=True,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
            self.transport = AcpTransport(self.process, self.directory)
            initialized = self.request('initialize', {'protocolVersion': 1,
                'clientInfo': {'name': 'uni-robot-agent', 'version': '1'},
                'clientCapabilities': {}}, self.startup_timeout_s)
            if initialized.get('protocolVersion') != 1:
                raise RuntimeError('Unsupported ACP protocol version')
            session = self.request('session/new', {'cwd': str(self.workspace), 'mcpServers': []},
                                   self.startup_timeout_s)
            self.session_id = session['sessionId']
            self.binding.thread_id = self.session_id
            for update in self.pending_updates:
                self.on_notification('session/update', update)
            self.pending_updates.clear()
            result = self.request('session/prompt', {'sessionId': self.session_id,
                'prompt': [{'type': 'text', 'text': prompt.user}]})
            if result.get('stopReason') != 'end_turn':
                self.cancel(result.get('stopReason') or 'unknown_stop')
            return self.result()
        except KeyboardInterrupt:
            self.cancel('interrupted')
            raise AgentInterrupted(self.result('Interrupted by user'))
        except Exception as error:
            self.cancel(self.stop_reason or 'agent_error')
            return self.result(str(error))

    def cancel(self, reason):
        super().cancel(reason)
        if self.transport is not None and self.session_id:
            try:
                self.transport.notify('session/cancel', {'sessionId': self.session_id})
            except (BrokenPipeError, OSError, ValueError):
                pass  # Cleanup terminates the owned process even if ACP is unavailable.

    def close(self):
        try:
            super().close()
        finally:
            if self.transport is not None:
                self.transport.close()
                self.transport = None
