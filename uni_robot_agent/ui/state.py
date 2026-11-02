"""Project AgentActivity into presentation-ready state."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field, replace
from typing import Any

from uni_robot_agent.events import AgentActivity


@dataclass
class AgentSnapshot:
    agent_id: str
    parent_agent_id: str = ""
    label: str = ""
    status: str = "starting"
    current_tools: tuple[str, ...] = ()
    last_message: str = ""
    started_at: float = 0.0
    updated_at: float = 0.0

    @property
    def display_name(self) -> str:
        return self.label or self.agent_id[:8]

    def elapsed_s(self, now: float | None = None) -> float:
        return max(0.0, (now or time.time()) - self.started_at)


class AgentView:
    """Thread-safe read-only projection, independent of agent creation and scheduling."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._agents: dict[str, AgentSnapshot] = {}
        self._active_tools: dict[str, dict[str, str]] = {}

    def apply(self, event: AgentActivity) -> None:
        if not event.agent_id:
            return
        with self._lock:
            state = self._agents.get(event.agent_id)
            if state is None:
                state = AgentSnapshot(
                    agent_id=event.agent_id,
                    started_at=event.timestamp_unix_s,
                )
                self._agents[event.agent_id] = state
            active_tools = self._active_tools.setdefault(event.agent_id, {})
            if event.parent_agent_id:
                state.parent_agent_id = event.parent_agent_id
            if event.label:
                state.label = event.label
            state.updated_at = event.timestamp_unix_s

            if event.kind == "spawned":
                state.status = "starting"
            elif event.kind in {
                "started",
                "running",
                "tool_batch",
                "tool_started",
                "tool_completed",
                "tool_failed",
                "message",
            }:
                state.status = "running"
            elif event.kind == "idle":
                state.status = "idle"
                active_tools.clear()
            elif event.kind == "completed":
                state.status = "completed"
                active_tools.clear()
            elif event.kind == "closed":
                state.status = "closed"
                active_tools.clear()
            elif event.kind == "failed":
                state.status = "failed"
                active_tools.clear()

            if event.kind == "tool_started" and event.item_id and event.tool_names:
                active_tools[event.item_id] = event.tool_names[0]
            elif event.kind in {"tool_completed", "tool_failed"}:
                active_tools.pop(event.item_id, None)
            state.current_tools = tuple(active_tools.values())
            if event.text:
                state.last_message = _one_line(event.text)

    def children(self) -> tuple[AgentSnapshot, ...]:
        with self._lock:
            states = (
                replace(state)
                for state in self._agents.values()
                if state.parent_agent_id
            )
            return tuple(sorted(states, key=lambda state: state.started_at))



def _one_line(text: str, limit: int = 120) -> str:
    value = " ".join(text.split())
    return value if len(value) <= limit else value[: limit - 3] + "..."


@dataclass
class TaskStatus:
    """Plan and resource queue snapshots, without terminal rendering objects."""

    plan: tuple[dict[str, Any], ...] = ()
    queues: dict[str, Any] = field(default_factory=dict)

    def clear(self) -> None:
        self.plan = ()
        self.queues = {}

    def update_plan(self, plan) -> None:
        self.plan = tuple(dict(item) for item in plan)

    def update_queues(self, resources) -> None:
        self.queues = dict(resources)
