"""Native Anthropic Messages transport, with lossless request/response bodies."""
from __future__ import annotations

import copy
import json
from urllib.parse import urlsplit



def message_response(body: bytes) -> dict:
    """Read complete JSON or SSE messages without rewriting the delivered bytes."""
    text = body.decode("utf-8")
    if text.lstrip().startswith("{"):
        message = json.loads(text)
        if message.get("type") == "error":
            return message
        if message.get("type") != "message" or not message.get("stop_reason"):
            raise ValueError("Messages response has no terminal message")
        return message

    message = None
    blocks = {}
    fragments = {}
    open_blocks = set()
    stopped = False
    for packet in text.replace("\r\n", "\n").split("\n\n"):
        lines = [line[5:].lstrip() for line in packet.splitlines() if line.startswith("data:")]
        if not lines:
            continue
        event = json.loads("\n".join(lines))
        kind = event.get("type")
        if kind == "error":
            return event
        if kind == "ping":
            continue
        if stopped:
            raise ValueError("Messages stream continued after message_stop")
        if kind == "message_start":
            if message is not None:
                raise ValueError("Duplicate message_start")
            message = copy.deepcopy(event["message"])
        elif message is None:
            raise ValueError("Messages stream has no message_start")
        elif kind == "content_block_start":
            index = event["index"]
            if index in blocks:
                raise ValueError("Duplicate content block")
            blocks[index] = copy.deepcopy(event["content_block"])
            open_blocks.add(index)
        elif kind == "content_block_delta":
            index = event["index"]
            if index not in open_blocks:
                raise ValueError("Delta outside an open content block")
            delta = event["delta"]
            if delta["type"] == "input_json_delta":
                fragments[index] = fragments.get(index, "") + delta["partial_json"]
            elif delta["type"] in {"text_delta", "thinking_delta", "signature_delta"}:
                key = delta["type"].removesuffix("_delta")
                blocks[index][key] = blocks[index].get(key, "") + delta[key]
        elif kind == "content_block_stop":
            index = event["index"]
            if index not in open_blocks:
                raise ValueError("Stop outside an open content block")
            open_blocks.remove(index)
            if fragments.get(index):
                blocks[index]["input"] = json.loads(fragments[index])
        elif kind == "message_delta":
            message.update(event.get("delta", {}))
            message.setdefault("usage", {}).update(event.get("usage", {}))
        elif kind == "message_stop":
            stopped = True
    if message is None or not stopped or open_blocks or not message.get("stop_reason"):
        raise ValueError("Incomplete Messages stream")
    message["content"] = [blocks[index] for index in sorted(blocks)]
    return message


def response_failure(body: bytes) -> dict | None:
    try:
        message = message_response(body)
        if message.get("type") == "error":
            error = message.get("error") or {}
            return {"code": error.get("type", "messages_error"), "message": error.get("message", str(error))}
        ids = set()
        for block in message.get("content", []):
            if block.get("type") != "tool_use":
                continue
            if not block.get("id") or block["id"] in ids or not isinstance(block.get("input"), dict):
                raise ValueError("Invalid or duplicate Messages tool call")
            ids.add(block["id"])
        return None
    except (ValueError, KeyError, TypeError) as error:
        return {"code": "invalid_messages_response", "message": str(error)}


def response_usage(body: bytes) -> dict:
    try:
        return message_response(body).get("usage", {})
    except (ValueError, KeyError, TypeError):
        return {}


class MessagesProtocol:
    name = "messages"
    trace_client = "claude"
    response_failure = staticmethod(response_failure)
    response_usage = staticmethod(response_usage)

    def __init__(self, *, keepalive_interval_s=None):
        self.keepalive_interval_s = keepalive_interval_s

    def buffered_stream_response(self, body, request, forward):
        if self.keepalive_interval_s is None or not isinstance(body, dict) or body.get("stream") is not True:
            return None
        from .messages_keepalive import BufferedMessagesResponse
        return BufferedMessagesResponse(request, forward, self.keepalive_interval_s)

    def matches(self, path):
        return urlsplit(path).path.rstrip("/") in {"/messages", "/v1/messages"}

    def generation_request(self, request, parallel_tool_calls):
        if not isinstance(request, dict) or not isinstance(request.get("messages"), list):
            raise ValueError("Expected a Messages request")
        result = copy.deepcopy(request)
        if not parallel_tool_calls and result.get("tools"):
            choice = result.setdefault("tool_choice", {"type": "auto"})
            choice["disable_parallel_tool_use"] = True
        return result

    def validate_context(self, context_manager):
        if context_manager.policy_metadata["name"] != "identity":
            raise ValueError("Messages currently supports only identity context policy")

    def exception_response(self, error):
        return self.error_response({"message": str(error)})

    def error_response(self, failure):
        return {"type": "error", "error": {"type": "api_error", "message": failure["message"]}}
