"""Serial execution adapter for Toolkit."""

from __future__ import annotations

import threading
from uuid import uuid4
from typing import Any

from uni_robot_agent.execution import ExecutionResult, ToolExecutor, execution_context
from uni_robot_agent.telemetry import NullTracer
from uni_robot_agent.tools.toolkit import Toolkit


class SerialToolExecutor(ToolExecutor):
    """Serialize complete Toolkit calls.

    Toolkit owns handlers, ToolOutput, event emission, and step allocation;
    this runtime prevents overlap across entire calls.
    """

    def __init__(self, toolkit: Toolkit, *, tracer=None) -> None:
        self._toolkit = toolkit
        self._lock = threading.RLock()
        self._tracer = tracer or NullTracer()

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return self._toolkit.get_tool_schemas()

    def call(self, name: str, arguments: dict[str, Any]) -> ExecutionResult:
        # Execution locking belongs to runtime, not MCP; parallel runtimes choose
        # which resources require exclusion and which calls may overlap.
        execution_id = uuid4().hex
        self.emit_execution("queued", execution_id, name, arguments, "serial")
        with self._lock, execution_context(execution_id):
            self.emit_execution("started", execution_id, name, arguments, "serial")
            output = None
            error = None
            try:
                if not self._tracer.enabled:
                    output = self._toolkit.execute_tool(name, arguments)
                else:
                    with self._tracer.span("tool.execute", tool=name,
                            provider=self._toolkit.get_tool_provider_id(name), mode="immediate"):
                        output = self._toolkit.execute_tool(name, arguments)
                return ExecutionResult.completed(output)
            except Exception as exc:
                error = str(exc)
                raise
            finally:
                self.emit_execution("finished", execution_id, name, arguments, "serial",
                    success=output.success if output is not None else False,
                    error=output.error if output is not None else error)

    @property
    def tool_execution_seq(self) -> int:
        return self._toolkit.tool_execution_seq

    def close(self) -> None:
        # Sessions own Toolkit and environment resources; runtime must not close them again.
        pass
