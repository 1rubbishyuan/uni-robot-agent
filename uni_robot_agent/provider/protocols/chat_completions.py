"""Chat Completions response parsing and upstream error inspection."""
from __future__ import annotations
import json
from typing import Any
from uni_robot_agent.provider.protocols.sse import decode_events

def parse_response(response: bytes) -> dict[str, Any]:
    """Parse the upstream response once, including a trailing usage-only chunk."""
    try:
        body = json.loads(response)
    except ValueError:
        decoded = decode_events(response)
        if decoded is None:
            raise ValueError("Invalid Chat Completions response")
        chunks, _ = decoded
        body = _aggregate_chunks(chunks)
    return body


def _aggregate_chunks(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    reasoning, text = "", ""
    calls: dict[str, dict[str, Any]] = {}
    index_keys: dict[int, str] = {}
    id_keys: dict[str, str] = {}
    anonymous_index = 0
    response_id, usage = "resp_chat", None
    for chunk in chunks:
        response_id = chunk.get("id") or response_id
        usage = chunk.get("usage") or usage
        delta = (chunk.get("choices") or [{}])[0].get("delta") or {}
        reasoning += delta.get("reasoning_content") or ""
        text += delta.get("content") or ""
        for call in delta.get("tool_calls") or []:
            call_id, index = call.get("id"), call.get("index")
            # IDs and stream indexes identify calls across chunks; chunk position does not.
            key = id_keys.get(call_id) if call_id else None
            if key is None and index is not None:
                key = index_keys.get(index)
                if key is not None and call_id and calls[key]["id"] not in {None, call_id}:
                    key = None
            if key is None and call_id:
                key = f"id:{call_id}"
            if key is None and index is not None:
                key = f"index:{index}"
            if key is None:
                key = f"anonymous:{anonymous_index}"
                anonymous_index += 1
            value = calls.setdefault(key, {"id": call_id, "name": "", "arguments": ""})
            function = call.get("function") or {}
            value["id"] = value["id"] or call_id
            value["name"] += function.get("name") or ""
            value["arguments"] += function.get("arguments") or ""
            if call_id:
                id_keys[call_id] = key
            if index is not None:
                index_keys[index] = key
    return {"id": response_id, "usage": usage, "choices": [{"message": {
        "reasoning_content": reasoning, "content": text,
        "tool_calls": [{"id": call["id"], "function": {
            "name": call["name"], "arguments": call["arguments"],
        }} for call in calls.values()],
    }}]}


def response_failure(response: bytes) -> dict[str, str] | None:
    """Read explicit upstream Chat failures without treating model prose as errors."""
    decoded = decode_events(response)
    if decoded is not None:
        events, _ = decoded
        payloads = [
            event.get("response", {}) for event in events
            if event.get("type") == "response.failed"
        ] + [
            event for event in events
            # Chat streams can report a top-level error without an event type.
            if event.get("type") == "error" or event.get("error") is not None
        ]
    else:
        try:
            payloads = [json.loads(response)]
        except (ValueError, UnicodeDecodeError):
            return None
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        error = payload.get("error")
        if error is None and payload.get("status") != "failed" and payload.get("type") != "error":
            continue
        detail = error if isinstance(error, dict) else payload
        return {
            "code": str(detail.get("code") or "provider_response_failed"),
            "message": str(detail.get("message") or error or "Provider response failed"),
        }
    return None
