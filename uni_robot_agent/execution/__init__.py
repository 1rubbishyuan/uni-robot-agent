"""Public execution-runtime interfaces and shared call results."""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from uni_robot_agent.events import EventBus, ToolQueueSnapshot
from uni_robot_agent.tools.toolkit import ToolOutput


_execution_id: ContextVar[str | None] = ContextVar("tool_execution_id", default=None)


def current_execution_id() -> str | None:
    """Return the executing tool identity, independent of SDK and HTTP IDs."""
    return _execution_id.get()


@contextmanager
def execution_context(execution_id: str):
    """Bind identity in the worker that actually invokes the tool handler."""
    token = _execution_id.set(execution_id)
    try:
        yield
    finally:
        _execution_id.reset(token)


@dataclass(frozen=True)
class ExecutionResult:
    """Runtime result separating tool outcomes from background job states."""

    status: str
    output: ToolOutput | None = None
    job_id: str | None = None
    resource_key: str | None = None
    queue_position: int | None = None

    @classmethod
    def completed(cls, output: ToolOutput) -> "ExecutionResult":
        return cls(status="completed", output=output)

    @classmethod
    def accepted(
        cls,
        job_id: str,
        resource_key: str,
        status: str,
        queue_position: int | None = None,
    ) -> "ExecutionResult":
        return cls(
            status=status,
            job_id=job_id,
            resource_key=resource_key,
            queue_position=queue_position,
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"status": self.status}
        if self.job_id is not None:
            result["job_id"] = self.job_id
        if self.resource_key is not None:
            result["resource"] = self.resource_key
        if self.queue_position is not None:
            result["queue_position"] = self.queue_position
        if self.output is not None:
            result["output"] = self.output.to_dict()
        return result


@dataclass(frozen=True)
class ToolLifecycleEvent:
    """Execution lifecycle facts independent of environments and observation implementations."""

    phase: Literal["started", "finished"]
    execution_id: str
    tool_name: str
    arguments: dict[str, Any]
    resource_key: str
    mutates_world: bool
    job_id: str | None = None
    output: ToolOutput | None = None
    error: str | None = None


class ToolLifecycleListener(Protocol):
    """Optional lifecycle listener whose failures must not affect tool calls."""

    def on_tool_event(self, event: ToolLifecycleEvent) -> None:
        ...


QueueSnapshotListener = Callable[[dict[str, Any]], None]


class ToolExecutor(ABC):
    """Minimal interface for tool execution.

    Keep environment and MCP concepts out of this layer so Codex,
    Claude, and MCP bridges can share serial and parallel runtime implementations.
    """

    def bind_event_bus(self, bus) -> None:
        self._event_bus = bus

    def emit_execution(self, phase, execution_id, name, arguments, resource, *, job_id=None, success=None, error=None):
        from uni_robot_agent.events import ToolExecution
        from uni_robot_agent.execution.correlation import current_call
        bus = getattr(self, "_event_bus", None)
        if bus is not None:
            bus.emit(ToolExecution(phase=phase, execution_id=execution_id,
                tool_name=name, arguments=dict(arguments), resource=resource,
                job_id=job_id or "", success=success, error=error or "", **current_call()))

    @abstractmethod
    def get_tool_schemas(self) -> list[dict[str, Any]]:
        """Return tool schemas visible to the current agent."""
        ...

    @abstractmethod
    def call(self, name: str, arguments: dict[str, Any]) -> ExecutionResult:
        """Execute one call and return a runtime result."""
        ...

    def submit(self, name: str, arguments: dict[str, Any]) -> ExecutionResult:
        """Submit through the same policies; serial executors complete inline."""
        return self.call(name, arguments)

    def job_result(self, job_id: str, timeout: float | None = 0) -> ExecutionResult:
        """Wait for a submitted job; timeout does not cancel physical work."""
        raise KeyError(job_id)

    def cancel_job(self, job_id: str) -> bool:
        """Cancel only queued work; running physical actions are not interrupted."""
        raise KeyError(job_id)

    @property
    @abstractmethod
    def tool_execution_seq(self) -> int:
        """Return the current execution sequence number for event recording."""
        ...

    def add_queue_listener(self, listener: QueueSnapshotListener) -> None:
        """Serial runtimes have no background queue to publish."""

    def close(self) -> None:
        """Release owned resources; implementations without resources need not override."""
        pass



__all__ = [
    "ToolExecutor",
    "ExecutionResult",
    "ToolLifecycleEvent",
    "ToolLifecycleListener",
    "QueueSnapshotListener",
    "SerialToolExecutor",
    "ParallelToolExecutor",
]

def connect_execution_events(executor: ToolExecutor, bus: EventBus) -> None:
    """Publish actual runtime queue state to the shared event stream."""
    executor.bind_event_bus(bus)
    executor.add_queue_listener(
        lambda snapshot: bus.emit(
            ToolQueueSnapshot(resources=snapshot.get("resources", {})),
        ),
    )


# Define public contracts before importing implementations that refer back to this module.
from uni_robot_agent.execution.parallel import ParallelToolExecutor
from uni_robot_agent.execution.serial import SerialToolExecutor
