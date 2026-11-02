"""Responses request handling, usage parsing, error detection and tool-event ordering."""
from urllib.parse import urlsplit
import json
from typing import Any
from uni_robot_agent.provider.protocols.sse import decode_events, encode_events


def response_usage(response: bytes) -> dict[str, Any] | None:
    """Read usage from a normalized Responses JSON body or completed SSE event."""
    decoded = decode_events(response)
    if decoded is None:
        try:
            payload = json.loads(response)
        except (ValueError, UnicodeDecodeError):
            return None
    else:
        events, _ = decoded
        payload = next(
            (event.get("response") for event in events
             if event.get("type") == "response.completed"),
            None,
        )
    usage = payload.get("usage") if isinstance(payload, dict) else None
    return usage if isinstance(usage, dict) else None


def response_failure(response: bytes) -> dict[str, str] | None:
    """Read explicit provider failures from Responses JSON or SSE payloads."""
    decoded = decode_events(response)
    if decoded is not None:
        events, _ = decoded
        payloads = [
            event.get("response", {}) for event in events
            if event.get("type") == "response.failed"
        ] + [
            event for event in events
            # Providers may report a top-level error without an event type.
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


def order_function_call_completions(response: bytes) -> bytes:
    """Publish function-call completion events in final output order."""
    decoded = decode_events(response)
    if decoded is None:
        return response
    events, has_done = decoded

    completed = next(
        (event for event in events if event.get("type") == "response.completed"),
        None,
    )
    if completed is None:
        return response

    output = completed.get("response", {}).get("output", ())
    call_indexes = tuple(
        index
        for index, item in enumerate(output)
        if isinstance(item, dict) and item.get("type") == "function_call"
    )
    if len(call_indexes) < 2:
        return response

    terminals: dict[int, list[dict[str, Any]]] = {}
    remaining: list[dict[str, Any]] = []
    for event in events:
        output_index = event.get("output_index")
        if _is_function_terminal(event, call_indexes):
            terminals.setdefault(output_index, []).append(event)
        else:
            remaining.append(event)

    # Complete responses need a terminal for each call; pass incomplete streams through to the SDK.
    if set(terminals) != set(call_indexes):
        return response

    ordered: list[dict[str, Any]] = []
    for event in remaining:
        if event is completed:
            for output_index in call_indexes:
                ordered.extend(terminals[output_index])
        ordered.append(event)

    for sequence_number, event in enumerate(ordered):
        event["sequence_number"] = sequence_number
    return encode_events(ordered, has_done=has_done)


def _is_function_terminal(
    event: dict[str, Any],
    call_indexes: tuple[int, ...],
) -> bool:
    if event.get("output_index") not in call_indexes:
        return False
    event_type = event.get("type")
    if event_type == "response.function_call_arguments.done":
        return True
    return (
        event_type == "response.output_item.done"
        and event.get("item", {}).get("type") == "function_call"
    )


class ResponsesProtocol:
    name = "responses"
    trace_client = "codex"
    response_failure = staticmethod(response_failure)
    response_usage = staticmethod(response_usage)

    def buffered_stream_response(self, body, request, forward):
        return None

    def error_response(self, failure):
        return {"error": failure}

    def exception_response(self, error):
        return {"error": str(error)}

    def matches(self, path):
        return urlsplit(path).path.endswith("/responses")

    def generation_request(self, request, parallel_tool_calls):
        return {**(request or {}), "parallel_tool_calls": parallel_tool_calls}

    def validate_context(self, context_manager):
        pass
