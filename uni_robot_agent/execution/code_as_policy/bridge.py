"""Adapt the existing executor to blocking program calls without owning it."""
from __future__ import annotations

from threading import Event, Lock

from uni_robot_agent.execution import ExecutionResult, ToolExecutor
from uni_robot_agent.services.mcp.http import HttpMcpServer
from uni_robot_agent.tools.client import ToolClient


class DirectSkillTools(ToolExecutor):
    """Expose registered skill readers while robot operations stay in programs."""

    def __init__(self, bridge: CodePolicyBridge):
        self._bridge = bridge

    def get_tool_schemas(self):
        return [spec for spec in self._bridge.get_tool_schemas()
                if spec["name"] in {"list_skills", "read_skill"}]

    def call(self, name, arguments):
        if name not in {spec["name"] for spec in self.get_tool_schemas()}:
            raise RuntimeError("Only skill discovery and reading are available directly in code_as_policy mode")
        return self._bridge.call(name, arguments)

    @property
    def tool_execution_seq(self):
        return self._bridge.tool_execution_seq


class CodePolicyBridge(ToolExecutor):
    """Serialize program calls and retain only an executed finish receipt."""

    def __init__(self, executor: ToolExecutor, *, event_bus=None):
        self._executor = executor
        self._closed = Event()
        self._lock = Lock()
        self._client = ToolClient(executor, self._require_active)
        self._server = HttpMcpServer(self, event_bus=event_bus)
        self.finish_request: dict | None = None

    def start(self) -> str:
        return self._server.start()

    def revoke(self) -> None:
        # Never wait behind a physical action to revoke subsequent admissions.
        self._closed.set()

    def _require_active(self) -> None:
        if self._closed.is_set():
            raise RuntimeError("Code-as-policy session no longer accepts calls")

    def get_tool_schemas(self):
        return self._executor.get_tool_schemas()

    @property
    def tool_execution_seq(self):
        return self._executor.tool_execution_seq

    def call(self, name, arguments):
        with self._lock:
            self._require_active()
            try:
                output = self._client.call(name, arguments)
            except Exception:
                # An exception after submission does not establish zero execution.
                self.revoke()
                raise
            if (output.extra.get("execution_uncertain")
                    or output.extra.get("execution", {}).get("execution_uncertain")):
                self.revoke()
            if name == "finish" and output.success and not output.error and output.extra.get("submitted"):
                self.finish_request = dict(arguments)
                self.revoke()
            return ExecutionResult.completed(output)

    def close(self) -> None:
        self.revoke()
        self._server.stop()
        # Session owns executor draining and environment interruption/cleanup.
