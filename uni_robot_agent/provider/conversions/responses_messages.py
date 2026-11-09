"""Responses to native Anthropic Messages, preserving tool and image identity."""
from __future__ import annotations

import copy
import json

from uni_robot_agent.provider.contracts import EncodedRequest, DecodedResponse
from uni_robot_agent.provider.conversions.responses_chat import function_tool, encode_response
from uni_robot_agent.provider.conversions.tool_names import flatten_request, restore_response
from uni_robot_agent.provider.protocols.messages import message_response


def content_blocks(value):
    if isinstance(value, str):
        return [{"type": "text", "text": value}] if value else []
    if not isinstance(value, list):
        raise ValueError("Messages content must be text or content blocks")
    blocks = []
    for item in value:
        kind = item.get("type")
        if kind in {"input_text", "output_text", "text"}:
            if item.get("text"):
                blocks.append({"type": "text", "text": item["text"]})
        elif kind in {"input_image", "output_image", "image"}:
            url = item.get("image_url") or item.get("url")
            if isinstance(url, dict):
                url = url.get("url")
            if not url and item.get("data"):
                url = f"data:{item.get('mime_type') or item.get('mimeType')};base64,{item['data']}"
            if not isinstance(url, str):
                raise ValueError("Cannot forward image without data or URL")
            if url.startswith("data:"):
                header, data = url.split(",", 1)
                if not header.endswith(";base64"):
                    raise ValueError("Messages images require base64 data URLs")
                source = {"type": "base64", "media_type": header[5:-7], "data": data}
            else:
                source = {"type": "url", "url": url}
            blocks.append({"type": "image", "source": source})
        else:
            raise ValueError(f"Unsupported Messages content block: {kind!r}")
    return blocks


def encode_request(request, reasoning_history):
    flat, routes = flatten_request(request)
    messages, system, assistant, identities = [], [], [], []
    if flat.get("instructions"):
        system.extend(content_blocks(flat["instructions"]))

    def append(role, blocks):
        if not blocks:
            return
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"].extend(blocks)
        else:
            messages.append({"role": role, "content": blocks})

    def flush_assistant():
        if not assistant and not identities:
            return
        # Signed native blocks are keyed by response/tool identities, never by
        # text similarity or chronology. Only retained assistant turns replay them.
        thinking = []
        seen = set()
        for identity in identities:
            record = reasoning_history.get(identity)
            if record is not None and record[0] not in seen:
                thinking.extend(copy.deepcopy(record[1]))
                seen.add(record[0])
        append("assistant", thinking + assistant[:])
        assistant.clear()
        identities.clear()

    items = flat.get("input", [])
    if isinstance(items, str):
        items = [{"role": "user", "content": items}]
    for item in items:
        kind = item.get("type", "message" if "role" in item else None)
        if kind == "reasoning":
            identity = item.get("id")
            if identity and identity.startswith("rs_msg_") and identity not in reasoning_history:
                raise ValueError("Native Claude reasoning state is unavailable; start a new provider session")
            if identity:
                identities.append(identity)
        elif kind == "message":
            role = item["role"]
            blocks = content_blocks(item.get("content", ""))
            if role == "assistant":
                assistant.extend(blocks)
                if item.get("id"):
                    identities.append(item["id"])
            else:
                flush_assistant()
                if role in {"system", "developer"} and not messages:
                    system.extend(blocks)
                elif role in {"system", "developer"}:
                    append("user", [{"type": "text", "text": f"[{role} instruction]"}] + blocks)
                elif role == "user":
                    append("user", blocks)
                else:
                    raise ValueError(f"Unsupported Messages role: {role!r}")
        elif kind in {"function_call", "custom_tool_call"}:
            arguments = item.get("arguments")
            value = json.loads(arguments) if arguments is not None else {"input": item.get("input", "")}
            if not isinstance(value, dict):
                raise ValueError("Tool arguments must be an object")
            identities.append(item["call_id"])
            assistant.append({"type": "tool_use", "id": item["call_id"], "name": item["name"], "input": value})
        elif kind in {"function_call_output", "custom_tool_call_output"}:
            flush_assistant()
            append("user", [{"type": "tool_result", "tool_use_id": item["call_id"],
                             "content": content_blocks(item.get("output", ""))}])
        elif kind != "additional_tools":
            raise ValueError(f"Unsupported Responses input item for Messages: {kind!r}")
    flush_assistant()
    validate_messages(messages)
    tools = []
    for spec in flat.get("tools", []):
        if spec.get("type") == "web_search":
            raise ValueError("Native web search is not supported by the Messages conversion")
        fn = function_tool(spec)
        tools.append({"name": fn["name"], "description": fn.get("description", ""),
                      "input_schema": fn.get("parameters", {"type": "object", "properties": {}})})
    body = {"model": flat.get("model"), "messages": messages, "tools": tools,
            "max_tokens": request.get("max_output_tokens", request.get("max_tokens", 65536)),
            "stream": bool(request.get("stream", False))}
    if system:
        body["system"] = system
    for key in ("temperature", "top_p"):
        if key in request:
            body[key] = request[key]
    choice = request.get("tool_choice", "auto")
    if not isinstance(choice, str) or choice not in {"auto", "none"}:
        raise ValueError("Messages conversion supports only auto or none tool choice")
    if tools:
        body["tool_choice"] = {"type": choice}
        if request.get("parallel_tool_calls") is False:
            body["tool_choice"]["disable_parallel_tool_use"] = True
    training = copy.deepcopy(flat)
    return EncodedRequest(body, {"routes": routes, "responses_stream": body["stream"]}, training)


def decode_response(response, decode_state, reasoning_history):
    message = message_response(response)
    if message.get("type") == "error":
        error = message.get("error", {})
        raise ValueError(f"Native Messages error: {error.get('type')}: {error.get('message')}")
    stop = message.get("stop_reason")
    if stop not in {"end_turn", "tool_use", "stop_sequence", "max_tokens", "refusal"}:
        raise ValueError(f"Unsupported Messages terminal reason: {stop!r}")
    response_id = message["id"]
    blocks = message.get("content", [])
    thinking = [copy.deepcopy(b) for b in blocks if b["type"] in {"thinking", "redacted_thinking"}]
    outputs, ids, seen = [], [], set()
    if thinking:
        identity = "rs_" + response_id
        outputs.append({"type": "reasoning", "id": identity, "status": "completed",
                        "summary": [{"type": "summary_text", "text": b["thinking"]}
                                    for b in thinking if b.get("thinking")]})
        ids.append(identity)
    for index, block in enumerate(blocks):
        kind = block["type"]
        if kind == "text":
            identity = f"msg_{response_id}_{index}"
            outputs.append({"type": "message", "id": identity, "role": "assistant", "status": "completed",
                            "content": [{"type": "output_text", "text": block["text"], "annotations": []}]})
            ids.append(identity)
        elif kind == "tool_use":
            identity = block.get("id")
            if stop == "max_tokens" or not identity or identity in seen or not isinstance(block.get("input"), dict):
                raise ValueError("Invalid, duplicate or truncated Messages tool call")
            seen.add(identity)
            outputs.append({"type": "function_call", "id": "fc_" + identity, "call_id": identity,
                            "name": block["name"], "arguments": json.dumps(block["input"], ensure_ascii=False),
                            "status": "completed"})
            ids.append(identity)
        elif kind not in {"thinking", "redacted_thinking"}:
            raise ValueError(f"Unsupported native Messages block: {kind!r}")
    if thinking:
        record = (response_id, thinking)
        for identity in ids:
            reasoning_history[identity] = record
    usage = message.get("usage", {})
    input_tokens = sum(usage.get(k, 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    output_tokens = usage.get("output_tokens", 0)
    training = {"id": response_id, "object": "response", "model": message.get("model"),
                "status": "incomplete" if stop == "max_tokens" else "completed", "output": outputs,
                "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens,
                          "total_tokens": input_tokens + output_tokens,
                          "input_tokens_details": {"cached_tokens": usage.get("cache_read_input_tokens", 0)}}}
    if stop == "max_tokens":
        training["incomplete_details"] = {"reason": "max_output_tokens"}
    client = restore_response(training, decode_state["routes"])
    return DecodedResponse(encode_response(client, stream=decode_state["responses_stream"]),
                           encode_response(training, stream=decode_state["responses_stream"]))


def validate_messages(messages: list[dict]) -> None:
    """Require each native tool batch to be acknowledged before continuing."""
    seen, pending = set(), set()
    for message in messages:
        role = message["role"]
        blocks = message["content"]
        if role == "assistant":
            if pending:
                raise ValueError("Assistant continued before all tool results arrived")
            for block in blocks:
                if block["type"] == "tool_use":
                    identity = block["id"]
                    if not identity or identity in seen:
                        raise ValueError("Duplicate or empty Messages tool call ID")
                    seen.add(identity)
                    pending.add(identity)
        else:
            text_seen = False
            for block in blocks:
                if block["type"] == "tool_result":
                    identity = block["tool_use_id"]
                    if text_seen or identity not in pending:
                        raise ValueError("Unmatched, duplicate or misplaced Messages tool result")
                    pending.remove(identity)
                else:
                    text_seen = True
                    if pending:
                        raise ValueError("User content interrupted pending tool results")
            if pending:
                raise ValueError("Missing Messages tool results")
    if pending:
        raise ValueError("Missing Messages tool results")
