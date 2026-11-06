"""Owned subprocess resources shared by the two external CLI adapters."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import threading
import time
from uuid import uuid4

from uni_robot_agent.agents.contracts import AgentCapabilities, AgentResult
from uni_robot_agent.agents.tool_binding import DeliveredToolBinding
from uni_robot_agent.events import AgentRunStarted, ModelRequest
from uni_robot_agent.services.mcp.http import HttpMcpServer


def executable(value):
    path = shutil.which(value)
    if path is None:
        raise ValueError(f'Agent executable not found: {value}')
    return str(Path(path).absolute())


def isolated_environment(directory):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('OPENCODE_', 'GEMINI_', 'GOOGLE_', 'ANTHROPIC_', 'OPENAI_'))}
    for name, leaf in [('XDG_CONFIG_HOME', 'config'), ('XDG_DATA_HOME', 'data'),
                       ('XDG_CACHE_HOME', 'cache'), ('XDG_STATE_HOME', 'state')]:
        path = directory / leaf
        path.mkdir(parents=True, exist_ok=True)
        env[name] = str(path)
    return env


def stop_process(process):
    if process is None:
        return
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            process.wait(timeout=5)
            return
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)


def finalize_records(gateway, result, session_dir, event_bus):
    from uni_robot_agent.provider.recording.exchanges import get_model_exchange_error
    result.provider_error = get_model_exchange_error(gateway.trace_records,
        interrupted=bool(result.stop_reason))
    if result.provider_error:
        result.error = result.error or result.provider_error
        result.success = False
    if session_dir is not None:
        result.provider_log_path = str(Path(session_dir) / result.trace_format)
    result.train_error = 'Native exchanges are archived; validated SFT export is not implemented'
    gateway.persist_report({'trace_format': result.trace_format, 'records_frozen': True,
        'exchange_count': len(gateway.trace_records), 'provider_error': result.provider_error,
        'agent_error': result.error, 'train_error': result.train_error})


class ManagedAgent:
    capabilities = AgentCapabilities(tool_event_source='execution')
    text_separator = '\n'

    def __init__(self, *, model, binary, expected_version, read_calls,
                 timeout_s=10800, api_key=None, startup_timeout_s=30, context_policy="identity"):
        self.model, self.binary, self.expected_version = model, binary, expected_version
        self.timeout_s, self.startup_timeout_s = timeout_s, startup_timeout_s
        self.api_key = api_key
        self.context_policy = context_policy
        self.binding = DeliveredToolBinding(read_calls)
        self.stopped = threading.Event()
        self.stop_reason = None
        self.process = None
        self.mcp = None
        self.bus = None
        self.directory = None
        self.started = None
        self.texts = []

    def begin(self, prompt, task, executor, event_bus, session_dir, max_turns, prefixes):
        if self.started is not None:
            raise RuntimeError('Agent adapters support one episode per instance')
        if session_dir is None:
            raise ValueError('External CLI backends require a session directory for isolated state')
        if not prompt.system or not prompt.system.strip():
            raise ValueError('Robot system prompt must not be empty')
        self.started = time.monotonic()
        self.deadline = self.started + self.timeout_s if self.timeout_s else None
        self.directory = Path(session_dir).absolute() / self.name
        self.directory.mkdir(parents=True, exist_ok=True)
        self.workspace = self.directory / 'workspace'
        self.workspace.mkdir(exist_ok=True)
        self.binary = executable(self.binary)
        self.verify_runtime()
        self.env = isolated_environment(self.directory)
        version = subprocess.run([self.binary, '--version'], capture_output=True, text=True,
                                 timeout=self.startup_timeout_s, check=True, env=self.env).stdout.strip()
        if version != self.expected_version:
            raise ValueError(f'Expected {self.name} {self.expected_version}, found {version}')
        (self.directory / 'runtime.json').write_text(json.dumps({
            'backend': self.name, 'version': version, 'model': self.model,
            'context_policy': self.context_policy, 'turn_unit': 'delivered_model_exchange',
        }, indent=2) + '\n')
        (self.directory / 'system.md').write_text(prompt.system)
        (self.directory / 'user.md').write_text(prompt.user)
        self.bus = event_bus
        self.binding.bus = event_bus
        self.binding.configure_tools(executor.get_tool_schemas(), prefixes)
        self.binding.max_exchanges = max_turns
        self.mcp_token = uuid4().hex
        self.mcp = HttpMcpServer(executor, event_bus=event_bus,
            call_handler=lambda params: self.binding.dispatch(executor, params), auth_token=self.mcp_token)
        self.mcp_url = self.mcp.start()
        if self.bus:
            self.bus.subscribe(self.on_model_event)
            self.bus.emit(AgentRunStarted(instruction=task, model=self.model,
                agent_backend=self.name, tool_event_source=self.capabilities.tool_event_source))

    def on_model_event(self, event):
        if isinstance(event, ModelRequest) and event.phase == 'finished' and event.success is False:
            # Stop before a CLI's outer retry loop can multiply gateway attempts.
            self.cancel('max_turns' if self.binding.limit_reached else 'provider_error')

    def check_running(self):
        if self.binding.limit_reached:
            self.cancel('max_turns')
        if self.deadline is not None and time.monotonic() >= self.deadline:
            self.cancel('wall_time_limit')
        if self.stopped.is_set():
            raise RuntimeError('Agent stopped: ' + str(self.stop_reason))
        if self.process is not None and self.process.poll() is not None:
            raise RuntimeError(f'{self.name} process exited with status {self.process.returncode}')

    def verify_runtime(self):
        """Subclasses may require a reproducible compatibility artifact."""

    def cancel(self, reason):
        self.stop_reason = self.stop_reason or reason
        self.binding.cancel()
        self.stopped.set()

    def result(self, error=None):
        return AgentResult(success=self.binding.finish_submitted and error is None and not self.stop_reason,
            message=self.text_separator.join(self.texts), error=error, stop_reason=self.stop_reason,
            turns_used=self.binding.exchanges, tool_calls=self.binding.tool_calls,
            steps_used=self.binding.exchanges + self.binding.tool_calls,
            elapsed_s=time.monotonic() - self.started if self.started else 0,
            main_thread_id=self.binding.thread_id, trace_format=self.trace_format)

    def finalize_without_provider(self, result, session_dir):
        raise RuntimeError('External CLI backends require the model gateway')

    def close(self):
        self.binding.cancel()
        try:
            stop_process(self.process)
            if self.process is not None:
                for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                    if stream is not None:
                        stream.close()
            self.process = None
            # Killing the planner does not stop an executor worker.
            self.binding.drain()
        finally:
            try:
                if self.mcp is not None:
                    self.mcp.stop()
                    self.mcp = None
            finally:
                if self.bus:
                    self.bus.unsubscribe(self.on_model_event)
                    self.bus = None
