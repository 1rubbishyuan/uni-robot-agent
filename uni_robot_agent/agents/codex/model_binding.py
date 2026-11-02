"""Bind Codex Responses exchanges to agent identities and tool delivery events."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from uni_robot_agent.events import EventBus, ToolCallBatch
from uni_robot_agent.provider.contracts import PreparedReply, RequestContext
from uni_robot_agent.provider.protocols.responses import order_function_call_completions
from uni_robot_agent.telemetry.logging import get_logger

logger = get_logger("codex")

from .events import CodexAgentEventAdapter, _emit_events

def _emit_provider_batch(
    bus: EventBus | None,
    adapter: CodexAgentEventAdapter,
    batch_id: str,
    metadata: Mapping[str, Any],
    calls: tuple[dict[str, Any], ...],
) -> None:
    if bus is None:
        return
    activities = adapter.from_provider_batch(batch_id, metadata, calls)
    _emit_events(bus, activities)
    batch = activities[-1]
    # ToolCallBatch feeds the main-agent view; AgentActivity carries subagent batches.
    if adapter.is_main(batch.agent_id):
        bus.emit(
            ToolCallBatch(
                batch_id=batch_id,
                tool_names=tuple(str(call.get("name", "")) for call in calls),
                tool_calls=tuple(calls),
            )
        )

def _request_agent_metadata(request: dict[str, Any] | None) -> dict[str, str]:
    """Extract Codex thread relationships without inferring roles from identifiers."""
    if not request:
        return {}
    client = request.get("client_metadata")
    if not isinstance(client, dict):
        return {}
    embedded: dict[str, Any] = {}
    raw = client.get("x-codex-turn-metadata")
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
            if isinstance(value, dict):
                embedded = value
        except ValueError:
            pass
    result = {}
    for key in ("thread_id", "parent_thread_id", "turn_id", "thread_source"):
        value = client.get(key) or embedded.get(key)
        if value:
            result[key] = str(value)
    if "parent_thread_id" not in result:
        value = client.get("x-codex-parent-thread-id")
        if value:
            result["parent_thread_id"] = str(value)
    return result

def _tool_calls_from_codex_response(
    response: bytes,
) -> tuple[dict[str, Any], ...]:
    decoded = response.decode("utf-8", errors="replace")
    payloads: list[dict[str, Any]] = []
    if any(line.startswith("data:") for line in decoded.splitlines()):
        for line in decoded.splitlines():
            if not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if not raw or raw == "[DONE]":
                continue
            try:
                event = json.loads(raw)
            except ValueError:
                continue
            if event.get("type") == "response.completed":
                completed = event.get("response")
                if isinstance(completed, dict):
                    payloads = [completed]
                    break
            if event.get("type") == "response.output_item.done":
                item = event.get("item")
                if isinstance(item, dict):
                    payloads.append({"output": [item]})
    else:
        try:
            payload = json.loads(response)
        except (TypeError, ValueError):
            payload = None
        if isinstance(payload, dict):
            payloads.append(payload)

    calls: list[dict[str, Any]] = []
    seen: set[Any] = set()
    for payload in payloads:
        for item in payload.get("output") or ():
            if not isinstance(item, dict) or item.get("type") != "function_call":
                continue
            name = item.get("name")
            if not isinstance(name, str) or not name:
                continue
            key = item.get("call_id") or item.get("id") or (name, len(calls))
            if key in seen:
                continue
            seen.add(key)
            arguments = item.get("arguments", {})
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except ValueError:
                    pass
            calls.append(
                {
                    "name": name,
                    "call_id": str(item.get("call_id") or item.get("id") or ""),
                    "arguments": arguments,
                }
            )
    return tuple(calls)

class CodexResponsesBinding:
    """Codex request identity and tool delivery semantics for the HTTP gateway."""

    def __init__(
        self,
        event_bus: EventBus | None = None,
        sdk_events: CodexAgentEventAdapter | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._sdk_events = sdk_events or CodexAgentEventAdapter()

    def request_context(self, request: dict[str, Any] | None) -> RequestContext:
        # Compaction asks for the complete previous context, even under a sliding window.
        return RequestContext(
            metadata=_request_agent_metadata(request),
            preserve_history=_is_compaction_request(request or {}),
        )

    def prepare_response(
        self,
        body: bytes,
        exchange_id: str,
        metadata: dict[str, str],
    ) -> PreparedReply:
        ordered = order_function_call_completions(body)
        calls = _tool_calls_from_codex_response(ordered)
        try:
            self._sdk_events.prepare_provider_calls(calls)
        except Exception:
            logger.exception("Tool batch prepare callback failed")
        return PreparedReply(ordered, exchange_id, metadata, calls)

    def response_written(self, reply: PreparedReply) -> None:
        if reply.tool_calls:
            try:
                _emit_provider_batch(
                    self._event_bus,
                    self._sdk_events,
                    reply.exchange_id,
                    reply.metadata,
                    reply.tool_calls,
                )
            except Exception:
                logger.exception("Tool batch callback failed")

    def response_failed(self, reply: PreparedReply) -> None:
        try:
            self._sdk_events.discard_provider_calls(reply.tool_calls)
        except Exception:
            logger.exception("Tool batch cancel callback failed")

def _is_compaction_request(request: dict[str, Any]) -> bool:
    if request.get("tools"):
        return False
    value = request.get("input")
    if not isinstance(value, list):
        return False
    for item in reversed(value):
        if not isinstance(item, dict):
            continue
        if item.get("type") != "message" or item.get("role") != "user":
            continue
        return "CONTEXT CHECKPOINT COMPACTION" in _plain_text(item.get("content"))
    return False

def _plain_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(
            str(item.get("text", "")) for item in value if isinstance(item, dict)
        )
    return ""
