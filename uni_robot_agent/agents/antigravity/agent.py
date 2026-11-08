"""Run the Antigravity SDK loop with gateway-owned model delivery."""
from __future__ import annotations

import asyncio
import hashlib
from importlib.metadata import distribution
import json
import os
import sys
from pathlib import Path
import time
from uuid import uuid4

from uni_robot_agent.agents.contracts import AgentCapabilities, AgentInterrupted, AgentResult
from uni_robot_agent.agents.managed import finalize_records
from uni_robot_agent.agents.runtime import ModelRuntime
from uni_robot_agent.agents.tool_binding import DeliveredToolBinding
from uni_robot_agent.events import AgentRunStarted, AssistantText, ModelRequest
from uni_robot_agent.provider.backends.aliyun.gemini_chat_completions import AliyunGeminiChatAdapter
from uni_robot_agent.provider.protocols.antigravity import AntigravityProtocol
from uni_robot_agent.services.mcp.http import HttpMcpServer
from .config import AntigravityConfig, SDK_VERSION
from .hooks import build_hooks, delivered_calls


class AntigravityAgent(AntigravityConfig):
    name = 'antigravity'
    trace_format = 'antigravity_generate_content'
    capabilities = AgentCapabilities(tool_event_source='execution')

    def __init__(self, *, model, timeout_s=10800, api_key=None):
        self.model, self.timeout_s, self.api_key = model, timeout_s, api_key
        self.binding = DeliveredToolBinding(delivered_calls)
        self.loop = self.task = self.mcp = self.bus = None
        self.stop_reason = self.cleanup_error = None
        self.started = None
        self.texts = []

    def model_runtime(self, profile, event_bus=None):
        if profile != 'aliyun.gemini_chat_completions':
            raise ValueError('Antigravity requires the Qwen GenerateContent bridge')
        self.binding.bus = event_bus
        adapter = AliyunGeminiChatAdapter()
        adapter.api_key = self.api_key
        return ModelRuntime(self.trace_format, adapter, AntigravityProtocol(self.model),
                            self.binding, finalize_records)

    def finalize_without_provider(self, result, session_dir):
        raise RuntimeError('Antigravity requires the model gateway')

    def cancel(self, reason):
        self.stop_reason = self.stop_reason or reason
        self.binding.cancel()
        if self.loop is not None and self.task is not None and not self.task.done():
            self.loop.call_soon_threadsafe(self.task.cancel)

    def on_model_event(self, event):
        if isinstance(event, ModelRequest) and event.phase == 'finished' and event.success is False:
            self.cancel('max_turns' if self.binding.limit_reached else 'provider_error')

    def result(self, error=None):
        return AgentResult(
            success=self.binding.finish_submitted and not error and not self.stop_reason and not self.cleanup_error,
            message=''.join(self.texts), error=error, stop_reason=self.stop_reason,
            cleanup_error=self.cleanup_error, turns_used=self.binding.exchanges,
            tool_calls=self.binding.tool_calls, steps_used=self.binding.exchanges + self.binding.tool_calls,
            elapsed_s=time.monotonic() - self.started if self.started else 0,
            main_thread_id=self.binding.thread_id, trace_format=self.trace_format)

    def run(self, *, prompt, task, toolkit, executor, max_turns=0, event_bus=None,
            session_dir=None, model_endpoint=None, code_policy=None):
        if code_policy is not None or not model_endpoint or session_dir is None:
            raise ValueError('Antigravity requires tool_calling, a gateway and a session directory')
        if self.started is not None:
            raise RuntimeError('Antigravity instances support one episode')
        if not prompt.system.strip():
            raise ValueError('Robot system prompt must not be empty')
        self.started = time.monotonic()
        directory = Path(session_dir).absolute() / self.name
        directory.mkdir(parents=True, exist_ok=True)
        self.binding.configure_tools(executor.get_tool_schemas(), ('mcp_ura_',))
        if any(set(spec['input_schema'].get('properties', {})) & {'toolSummary', 'toolAction'}
               for spec in self.binding.specs.values()):
            raise ValueError('Tool arguments collide with Antigravity SDK UI labels')
        self.binding.max_exchanges = max_turns
        self.bus = self.binding.bus = event_bus
        token = uuid4().hex
        self.mcp = HttpMcpServer(executor, auth_token=token,
            call_handler=lambda params: self.binding.dispatch(executor, params))
        self.mcp.start()
        if self.bus:
            self.bus.subscribe(self.on_model_event)
            self.bus.emit(AgentRunStarted(instruction=task, model=self.model,
                agent_backend=self.name, tool_event_source=self.capabilities.tool_event_source))
        try:
            asyncio.run(self.run_sdk(prompt, directory, token, model_endpoint))
            return self.result()
        except KeyboardInterrupt:
            self.cancel('interrupted')
            raise AgentInterrupted(self.result('Interrupted by user'))
        except Exception as error:
            self.cancel('agent_error')
            return self.result(f'{type(error).__name__}: {error}')
        finally:
            self.loop = self.task = None

    async def run_sdk(self, prompt, directory, token, endpoint):
        from google.antigravity import Agent, LocalAgentConfig, types
        from google.antigravity.hooks import policy

        self.loop, self.task = asyncio.get_running_loop(), asyncio.current_task()
        dist = distribution('google-antigravity')
        if dist.version != SDK_VERSION:
            raise ValueError(f'Antigravity requires google-antigravity=={SDK_VERSION}')
        binary = Path(dist.locate_file('google/antigravity/bin/localharness')).resolve(strict=True)
        workspace = directory.parent / 'scratch'
        workspace.mkdir(exist_ok=True)
        (directory / 'system.md').write_text(prompt.system)
        (directory / 'user.md').write_text(prompt.user)
        server = types.McpStreamableHttpServer(name='ura', url=self.mcp.url,
            force_all_tools_eager=True, headers={'Authorization': 'Bearer ' + token},
            sse_read_timeout=self.timeout_s or 10800)
        native = [types.BuiltinTools.VIEW_FILE, types.BuiltinTools.LIST_DIR,
                  types.BuiltinTools.FIND_FILE, types.BuiltinTools.SEARCH_DIR,
                  types.BuiltinTools.CREATE_FILE, types.BuiltinTools.EDIT_FILE,
                  types.BuiltinTools.RUN_COMMAND]
        model = types.ModelTarget(name=self.model, types=[types.ModelType.TEXT, types.ModelType.IMAGE],
            endpoint=types.GeminiAPIEndpoint(base_url=endpoint.rstrip('/').removesuffix('/v1'),
                api_key='local-gateway', options=types.GeminiModelOptions(max_output_tokens=8192)))
        config = LocalAgentConfig(model=model, vertex=False,
            system_instructions=types.CustomSystemInstructions(text=prompt.system),
            # Tool-supplied images/calibration and the published scratch path live
            # under the session root, not the SDK's private state directory.
            workspaces=[str(directory.parent)],
            save_dir=str(directory / 'native'), app_data_dir=str(directory / 'app'),
            env={'ANTIGRAVITY_HARNESS_PATH': str(binary),
                 'PATH': str(Path(sys.executable).parent) + os.pathsep + os.environ.get('PATH', ''),
                 'NO_PROXY': '127.0.0.1,localhost',
                 'no_proxy': '127.0.0.1,localhost'},
            capabilities=types.CapabilitiesConfig(enable_subagents=False, enabled_tools=native,
                mcp_max_output_bytes=64 * 1024 * 1024,
                run_command_config=types.RunCommandConfig(enable_daemons=False, timeout_seconds=60)),
            mcp_servers=[server], policies=[policy.allow(server, list(self.binding.specs)),
                *[policy.allow(tool.value) for tool in native]], hooks=build_hooks(self.binding, directory),
            retry_config=types.RetryConfig(api_retry=types.ModelAPIRetryConfig(max_retries=0),
                model_output_retry=types.ModelOutputRetryConfig(max_retries=2)))
        (directory / 'runtime.json').write_text(json.dumps({
            'sdk_version': dist.version, 'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
            'model': self.model, 'context_policy': 'identity', 'native_compaction': 'sdk_default',
            'native_tools': [tool.value for tool in native], 'mcp_eager': True,
            'workspace': str(directory.parent), 'scratch': str(workspace),
            'calculation_python': sys.executable, 'command_timeout_s': 60,
            'turn_unit': 'delivered_model_exchange', 'system_instructions': 'replace',
            'model_output_retries': 2, 'api_retries': 0,
        }, indent=2) + '\n')
        response = None
        try:
            async with asyncio.timeout(self.timeout_s or None):
                async with Agent(config) as agent:
                    try:
                        response = await agent.chat(prompt.user)
                        async for chunk in response:
                            self.texts.append(chunk)
                            if self.bus:
                                self.bus.emit(AssistantText(text=chunk))
                        (directory / 'completion.json').write_text(json.dumps({
                            'stop_reason': response.stop_reason.value,
                            'native_conversation_id': agent.conversation.conversation_id,
                            'usage': response.usage_metadata.model_dump(mode='json'),
                        }, indent=2) + '\n')
                        if response.stop_reason != types.StopReason.UNSPECIFIED:
                            self.stop_reason = response.stop_reason.value.lower()
                    finally:
                        # Stop native generation before the SDK disconnects its process.
                        if response is not None and (self.stop_reason or self.task.cancelling()):
                            await response.cancel()
                        with (directory / 'native-events.jsonl').open('w') as archive:
                            for step in agent.conversation.history:
                                archive.write(step.model_dump_json() + '\n')
        except asyncio.CancelledError:
            self.stop_reason = self.stop_reason or 'cancelled'
        except TimeoutError:
            self.stop_reason = self.stop_reason or 'wall_time_limit'
        finally:
            self.binding.cancel()

    def close(self):
        if self.task is not None and not self.task.done():
            self.cancel('cancelled')
            raise RuntimeError('Antigravity run has not finished cleanup')
        self.binding.cancel()
        try:
            self.binding.drain()
        finally:
            if self.mcp is not None:
                self.mcp.stop()
                self.mcp = None
            if self.bus:
                self.bus.unsubscribe(self.on_model_event)
                self.bus = None
