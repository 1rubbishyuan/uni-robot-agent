"""Translate Codex SDK notifications into agent activity and tool-call identities."""

from __future__ import annotations

import json
import queue
import threading
from collections.abc import Mapping
from types import MethodType, TracebackType
from typing import Any, Self

from uni_robot_agent.events import AgentActivity, EventBus
from uni_robot_agent.telemetry.logging import get_logger

logger = get_logger("codex")


_STOP = object()
_SUPPORTED_VERSION = "0.147.0"

_TOOL_ITEMS = {"mcpToolCall", "dynamicToolCall", "commandExecution", "fileChange", "collabAgentToolCall"}

def _get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)

def _unwrap(value: Any) -> Any:
    return getattr(value, "root", value)

def _emit_events(bus: EventBus, events: tuple[Any, ...]) -> None:
    for event in events:
        bus.emit(event)

def _arguments(item: Any) -> dict[str, Any]:
    value = _get(item, "arguments")
    if value is None and _get(item, "type") == "collabAgentToolCall":
        return {
            key: found
            for key in ("prompt", "model", "reasoning_effort")
            if (found := _get(item, key)) is not None
        }
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}

def _tool_name(item: Any, item_type: str) -> str:
    tool = _enum_value(_get(item, "tool", ""))
    return _snake_name(tool) if tool else _snake_name(item_type)

def _item_timestamp(payload: Any, completed: bool) -> float | None:
    keys = (
        ("completed_at_ms", "completedAtMs")
        if completed
        else (
            "started_at_ms",
            "startedAtMs",
        )
    )
    value = _get_any(payload, *keys)
    return float(value) / 1000 if isinstance(value, int | float) else None

def _snake_name(value: str) -> str:
    result = []
    for char in value:
        if char.isupper():
            result.extend(("_", char.lower()))
        else:
            result.append(char)
    return "".join(result)

def _enum_value(value: Any) -> str:
    return str(value.value if hasattr(value, "value") else value)

def _get_any(value: Any, *keys: str) -> Any:
    for key in keys:
        found = _get(value, key)
        if found is not None:
            return found
    return None

class CodexAgentEventAdapter:
    """Interpret Codex events; leave scheduling, termination, and outcomes to the agent."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._main_agent_id = ""
        self._parents: dict[str, str] = {}
        self._started: set[str] = set()
        self._provider_calls: dict[str, dict[str, Any]] = {}

    def set_main_agent(self, agent_id: str) -> AgentActivity:
        with self._lock:
            self._main_agent_id = agent_id
            self._started.add(agent_id)
        return self._activity("started", agent_id, "", "", label="main")

    def is_main(self, agent_id: str) -> bool:
        return not agent_id or agent_id == self._main_agent_id

    def agent_id_from_payload(self, payload: Any) -> str:
        return str(_get_any(payload, "thread_id", "threadId") or self._main_agent_id)

    def prepare_provider_calls(
        self,
        calls: tuple[dict[str, Any], ...],
    ) -> None:
        """Register call_id before SDK delivery so subsequent tool events carry its identity."""
        with self._lock:
            self._provider_calls.update(
                {
                    str(call["call_id"]): dict(call)
                    for call in calls
                    if call.get("call_id")
                }
            )

    def discard_provider_calls(
        self,
        calls: tuple[dict[str, Any], ...],
    ) -> None:
        """Undo registration when delivery fails so nonexistent calls leave no identity entries."""
        with self._lock:
            for call in calls:
                if call_id := call.get("call_id"):
                    self._provider_calls.pop(str(call_id), None)

    def from_provider_batch(
        self,
        exchange_id: str,
        metadata: Mapping[str, Any],
        calls: tuple[dict[str, Any], ...],
    ) -> tuple[AgentActivity, ...]:
        agent_id = str(metadata.get("thread_id") or self._main_agent_id)
        parent_id = str(metadata.get("parent_thread_id") or "")
        turn_id = str(metadata.get("turn_id") or "")
        events = self._ensure_started(agent_id, parent_id, turn_id)
        events.append(
            AgentActivity(
                kind="tool_batch",
                agent_id=agent_id,
                parent_agent_id=parent_id,
                turn_id=turn_id,
                label=self._label(agent_id),
                exchange_id=exchange_id,
                tool_names=tuple(str(call.get("name", "")) for call in calls),
                tool_calls=calls,
            )
        )
        return tuple(events)

    def from_sdk_event(self, event: Any) -> tuple[AgentActivity, ...]:
        method = str(_get(event, "method", ""))
        payload = _get(event, "payload")
        if method in {"turn/started", "turn/completed"}:
            return self._from_turn(method, payload)
        if method not in {"item/started", "item/completed"}:
            return ()

        item = _unwrap(_get(payload, "item"))
        item_type = str(_get(item, "type", ""))
        completed = method == "item/completed"

        if item_type == "collabAgentToolCall" and completed:
            return self._from_collab_call(item, payload)
        if item_type == "subAgentActivity":
            return self._from_subagent_activity(item, payload)

        agent_id = self.agent_id_from_payload(payload)
        parent_id = self._parent(agent_id)
        turn_id = str(_get_any(payload, "turn_id", "turnId") or "")
        events = self._ensure_started(agent_id, parent_id, turn_id)

        if item_type == "agentMessage":
            if completed and (text := str(_get(item, "text", "")).strip()):
                events.append(
                    self._activity(
                        "message",
                        agent_id,
                        parent_id,
                        turn_id,
                        text=text,
                    )
                )
            return tuple(events)
        if item_type == "reasoning" or item_type not in _TOOL_ITEMS:
            return tuple(events)

        events.append(
            self._tool_activity(
                item,
                payload,
                agent_id=agent_id,
                parent_id=parent_id,
                turn_id=turn_id,
                completed=completed,
            )
        )
        return tuple(events)

    def _from_turn(self, method: str, payload: Any) -> tuple[AgentActivity, ...]:
        agent_id = self.agent_id_from_payload(payload)
        parent_id = self._parent(agent_id)
        turn = _unwrap(_get(payload, "turn"))
        turn_id = str(
            _get(turn, "id", "") or _get_any(payload, "turn_id", "turnId") or ""
        )
        events = self._ensure_started(agent_id, parent_id, turn_id)
        if method == "turn/started":
            events.append(
                self._activity(
                    "running",
                    agent_id,
                    parent_id,
                    turn_id,
                )
            )
        else:
            status = _enum_value(_get(turn, "status", ""))
            kind = "failed" if status == "failed" else "idle"
            events.append(self._activity(kind, agent_id, parent_id, turn_id))
        return tuple(events)

    def _from_collab_call(
        self,
        item: Any,
        payload: Any,
    ) -> tuple[AgentActivity, ...]:
        sender = str(
            _get_any(item, "sender_thread_id", "senderThreadId")
            or self.agent_id_from_payload(payload)
        )
        receivers = _get_any(item, "receiver_thread_ids", "receiverThreadIds") or ()
        tool = _enum_value(_get(item, "tool", ""))
        turn_id = str(_get_any(payload, "turn_id", "turnId") or "")
        events = self._ensure_started(sender, self._parent(sender), turn_id)
        events.append(
            self._tool_activity(
                item,
                payload,
                agent_id=sender,
                parent_id=self._parent(sender),
                turn_id=turn_id,
                completed=True,
            )
        )

        if tool == "spawnAgent":
            for child_id in map(str, receivers):
                with self._lock:
                    self._parents[child_id] = sender
                events.append(
                    self._activity(
                        "spawned",
                        child_id,
                        sender,
                        turn_id,
                    )
                )
        elif tool == "closeAgent":
            for child_id in map(str, receivers):
                events.append(
                    self._activity(
                        "closed",
                        child_id,
                        self._parent(child_id),
                        turn_id,
                    )
                )
        return tuple(events)

    def _tool_activity(
        self,
        item: Any,
        payload: Any,
        *,
        agent_id: str,
        parent_id: str,
        turn_id: str,
        completed: bool,
    ) -> AgentActivity:
        item_id = str(_get(item, "id", ""))
        item_type = str(_get(item, "type", ""))
        with self._lock:
            provider_call = self._provider_calls.get(item_id)
            if completed:
                provider_call = self._provider_calls.pop(item_id, provider_call)
        name = (
            str(provider_call.get("name", ""))
            if provider_call
            else _tool_name(item, item_type)
        )
        status = _enum_value(_get(item, "status", ""))
        failed = status in {"failed", "declined"} or _get(item, "success") is False
        kind = (
            "tool_failed"
            if completed and failed
            else ("tool_completed" if completed else "tool_started")
        )
        call = (
            dict(provider_call)
            if provider_call
            else {
                "name": name,
                "call_id": item_id,
                "arguments": _arguments(item),
            }
        )
        activity = self._activity(
            kind,
            agent_id,
            parent_id,
            turn_id,
            item_id=item_id,
            tool_names=(name,),
            tool_calls=(call,),
            timestamp=_item_timestamp(payload, completed),
        )
        # Codex 0.147 exposes MCP result metadata as field_meta on its typed model.
        # This explicit transport receipt also works when identical calls overlap.
        result = _get(item, "result")
        metadata = _get_any(result, "_meta", "field_meta")
        if isinstance(metadata, Mapping):
            identity = metadata.get("uni_robot_agent/call_id")
            if isinstance(identity, str):
                activity.transport_call_id = identity
        return activity

    def _from_subagent_activity(
        self,
        item: Any,
        payload: Any,
    ) -> tuple[AgentActivity, ...]:
        agent_id = str(_get_any(item, "agent_thread_id", "agentThreadId") or "")
        kind = _enum_value(_get(item, "kind", ""))
        mapped = {
            "started": "started",
            "interacted": "running",
            "interrupted": "failed",
        }.get(kind, "running")
        turn_id = str(_get_any(payload, "turn_id", "turnId") or "")
        parent_id = self._parent(agent_id)
        if mapped == "started":
            return tuple(self._ensure_started(agent_id, parent_id, turn_id))
        return (self._activity(mapped, agent_id, parent_id, turn_id),)

    def _ensure_started(
        self,
        agent_id: str,
        parent_id: str,
        turn_id: str,
    ) -> list[AgentActivity]:
        if not agent_id:
            return []
        with self._lock:
            if parent_id:
                self._parents[agent_id] = parent_id
            if agent_id in self._started:
                return []
            self._started.add(agent_id)
        return [self._activity("started", agent_id, parent_id, turn_id)]

    def _parent(self, agent_id: str) -> str:
        with self._lock:
            return self._parents.get(agent_id, "")

    def _label(self, agent_id: str) -> str:
        return "main" if self.is_main(agent_id) else ""

    def _activity(
        self,
        kind: str,
        agent_id: str,
        parent_id: str,
        turn_id: str,
        *,
        label: str = "",
        item_id: str = "",
        tool_names: tuple[str, ...] = (),
        tool_calls: tuple[dict[str, Any], ...] = (),
        text: str = "",
        timestamp: float | None = None,
    ) -> AgentActivity:
        activity = AgentActivity(
            kind=kind,
            agent_id=agent_id,
            parent_agent_id=parent_id,
            turn_id=turn_id,
            label=label or self._label(agent_id),
            item_id=item_id,
            tool_names=tool_names,
            tool_calls=tool_calls,
            text=text,
        )
        if timestamp is not None:
            activity.timestamp_unix_s = timestamp
        return activity

class CodexEventSource:
    """Copy session notifications without altering SDK routing."""

    def __init__(
        self,
        codex: Any,
        sdk_version: str,
        bus: EventBus,
        adapter: CodexAgentEventAdapter,
    ) -> None:
        if sdk_version != _SUPPORTED_VERSION:
            raise RuntimeError(
                "Codex event source requires "
                f"openai-codex=={_SUPPORTED_VERSION}, got {sdk_version}"
            )
        self._router = codex._client._router
        self._bus = bus
        self._adapter = adapter
        self._queue: queue.SimpleQueue[Any] = queue.SimpleQueue()
        self._original_route = self._router.route_notification
        self._worker = threading.Thread(
            target=self._consume,
            name="codex-events",
            daemon=True,
        )

    def __enter__(self) -> Self:
        original_route = self._original_route
        event_queue = self._queue

        def route_notification(_router: Any, notification: Any) -> None:
            # Copy before handing control back to the SDK; never consume the main turn's events.
            event_queue.put(notification)
            original_route(notification)

        self._router.route_notification = MethodType(
            route_notification,
            self._router,
        )
        self._worker.start()
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _tb: TracebackType | None,
    ) -> None:
        self._router.route_notification = self._original_route
        # Restore the entry point before the sentinel so all earlier notifications reach EventBus.
        self._queue.put(_STOP)
        self._worker.join()

    def _consume(self) -> None:
        while True:
            notification = self._queue.get()
            if notification is _STOP:
                return
            for event in self._adapter.from_sdk_event(notification):
                self._bus.emit(event)
