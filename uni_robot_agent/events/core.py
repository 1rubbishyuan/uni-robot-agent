"""Event types and EventBus publish/subscribe support.

Provide one live event stream for terminal views and JSONL persistence.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from uni_robot_agent.tools.toolkit import ImageAttachment


# Event types

@dataclass
class Event:
    timestamp_unix_s: float = field(default_factory=time.time)
    session_id: str = ""
    event_seq: int = 0


@dataclass
class SessionOpened(Event):
    integration: str = ""
    instruction: str = ""


@dataclass
class SessionClosed(Event):
    task_success: bool | None = None
    accepted: bool = True
    failure_category: str | None = None


# Agent run events
@dataclass
class AgentRunStarted(Event):
    integration: str = ""
    agent_backend: str = ""
    instruction: str = ""
    model: str = ""
    tool_event_source: Literal["agent", "execution"] = "execution"


@dataclass
class AgentRunFinished(Event):
    # Agent outcome: the agent finished normally rather than crashing.
    success: bool = False
    # Official episode outcome; None means the environment does not provide one.
    task_success: bool | None = None
    turns: int = 0
    steps: int = 0
    tool_calls: int = 0
    message: str = ""
    elapsed_s: float = 0.0


# Content events
@dataclass
class ReasoningSummary(Event):
    """Public model reasoning summary, not the complete hidden reasoning trace."""

    text: str = ""


@dataclass
class AssistantText(Event):
    text: str = ""
    # Agent content-item position, including messages and native tool calls.
    agent_step: int = 0
    turn: int = 0


@dataclass
class ToolCallBatch(Event):
    """Tools emitted by one provider response before individual calls run."""

    batch_id: str = ""
    tool_names: tuple[str, ...] = ()
    tool_calls: tuple[dict[str, Any], ...] = ()


@dataclass
class ToolQueueSnapshot(Event):
    """Runtime's active tasks; trajectory events retain completed history."""

    resources: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentActivity(Event):
    """Normalized agent activity without task-specific semantics."""

    kind: str = ""
    agent_id: str = ""
    parent_agent_id: str = ""
    turn_id: str = ""
    label: str = ""
    item_id: str = ""
    transport_call_id: str = ""
    exchange_id: str = ""
    tool_names: tuple[str, ...] = ()
    tool_calls: tuple[dict[str, Any], ...] = ()
    text: str = ""


@dataclass
class ToolCall(Event):
    call_id: str = ""
    batch_id: str = ""
    execution_id: str = ""
    tool_name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    # Legacy source-provided position; cannot correlate calls with execution results.
    source_step: int = 0
    turn: int = 0


@dataclass
class ToolResult(Event):
    call_id: str = ""
    batch_id: str = ""
    execution_id: str = ""
    tool_name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    success: bool = True
    output: Any = None
    images: tuple[ImageAttachment, ...] = field(default_factory=tuple, repr=False)
    tool_execution_seq: int = 0
    turn: int = 0


@dataclass
class ErrorEvent(Event):
    message: str = ""
    # Legacy source-provided position; no cross-source ordering guarantee.
    source_step: int = 0
    turn: int = 0


@dataclass
class ToolExecution(Event):
    """Actual runtime work, distinct from an SDK submission acknowledgement."""
    execution_id: str = ""
    call_id: str = ""
    batch_id: str = ""
    job_id: str = ""
    tool_name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    resource: str = ""
    phase: str = ""
    success: bool | None = None
    error: str = ""


@dataclass
class ModelRequest(Event):
    """Gateway request wall time includes transport, provider wait and retries."""
    exchange_id: str = ""
    agent_id: str = ""
    phase: str = ""
    success: bool | None = None


# ─── EventBus ─────────────────────────────────────────────────────────────────

class EventBus:
    """Thread-safe publish/subscribe bus."""

    def __init__(self):
        self._lock = threading.Lock()
        self._handlers: list[Callable[[Event], None]] = []
        self._history: list[Event] = []
        self._next_seq = 1

    def subscribe(self, handler: Callable[[Event], None]) -> None:
        with self._lock:
            self._handlers.append(handler)

    def unsubscribe(self, handler: Callable[[Event], None]) -> None:
        with self._lock:
            self._handlers = [h for h in self._handlers if h is not handler]

    def emit(self, event: Event) -> None:
        with self._lock:
            # Gateway and SDK streams use different threads; assign sequence numbers centrally.
            event.event_seq = self._next_seq
            self._next_seq += 1
            self._history.append(event)
            handlers = list(self._handlers)
        for h in handlers:
            h(event)

    def replay(self) -> list[Event]:
        """Return event history so late subscribers can catch up."""
        with self._lock:
            return list(self._history)
