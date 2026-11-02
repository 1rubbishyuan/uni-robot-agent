"""Convert Codex Responses exchanges into SFT trajectories."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from uni_robot_agent.provider.recording.images import IMAGE_REF_KEY
from uni_robot_agent.provider.conversions.tool_names import qualified_tool_name
from uni_robot_agent.provider.recording.exchanges import (
    exchange_failure_detail, is_immediate_retry, iter_adjacent_exchanges,
    response_error_detail,
)
from uni_robot_agent.telemetry.logging import get_logger

logger = get_logger("codex_sft")


# Responses built-ins lack ordinary function name/parameters schemas. The training view
# uses named functions accepted by both ms-swift and the existing tool_call mapping.
# Extend this registry for new built-ins instead of scattering provider-specific cases.
_BUILTIN_TOOL_DEFINITIONS: dict[str, dict[str, Any]] = {
    "web_search": {
        "name": "web_search",
        "description": "Search the web.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
        "call_type": "web_search_call",
    },
}


@dataclass(frozen=True)
class CodexSftExport:
    """Training sample paths and whether a terminal error truncated the sample."""

    path: Path
    partial: bool = False
    stop_reason: str | None = None


def write_codex_sft_sample(
    exchanges: Iterable[dict[str, Any]],
    session_dir: Path | str,
) -> CodexSftExport:
    """Convert validated Responses context into one ms-swift sample."""
    # Read the exchange's model view without inferring prior protocol conversions;
    # protocol routing metadata belongs to the raw Codex trace, not the model view.
    session_dir = Path(session_dir)
    requests, outputs, tools, stop_reason = _parse_exchanges(exchanges)
    sample = _build_train_sample(requests, outputs, tools)
    path = session_dir / "train.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(sample, ensure_ascii=False, separators=(",", ":")) + "\n"
    with path.open("a", encoding="utf-8") as stream:
        stream.write(line)
    return CodexSftExport(
        path=path,
        partial=stop_reason is not None,
        stop_reason=stop_reason,
    )


def write_codex_sft_debug_sample(
    exchanges: Iterable[dict[str, Any]],
    session_dir: Path | str,
    export_error: str,
) -> Path:
    """Save a lightweight recoverable trajectory that is not directly trainable."""
    session_dir = Path(session_dir)
    path = session_dir / "train.invalid.jsonl"
    issues: list[dict[str, Any]] = []
    stop_reason: str | None = None
    try:
        requests, outputs, tools, stop_reason = _parse_exchanges(
            exchanges,
            strict=False,
            issues=issues,
        )
        sample = _build_train_sample(
            requests,
            outputs,
            tools,
            strict=False,
            issues=issues,
        )
    except Exception as exc:
        # Even unrecoverable context gets only a diagnostic summary; full wire data stays
        # in codex_responses, without duplicating cumulative requests or images.
        sample = {"messages": [], "tools": "[]", "images": []}
        issues.append({
            "type": "recovery_failed",
            "detail": f"{type(exc).__name__}: {exc}",
        })
    sample.update({
        "valid_for_sft": False,
        "partial": True,
        "export_error": export_error,
        "stop_reason": stop_reason,
        "issues": issues,
        "trace_dir": "codex_responses",
    })
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(sample, ensure_ascii=False, separators=(",", ":")) + "\n")
    return path


def _build_train_sample(
    requests: list[dict[str, Any]],
    outputs: list[list[dict[str, Any]]],
    tool_snapshot: list[Any],
    *,
    strict: bool = True,
    issues: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Share message, tool, and image mappings across strict and recovery exports."""
    issues = issues if issues is not None else []
    try:
        tools = _normalize_tools(tool_snapshot)
    except ValueError as exc:
        if strict:
            raise
        tools = []
        issues.append({"type": "invalid_tool_schema", "detail": str(exc)})

    images: list[str] = []
    messages = _build_messages(
        requests,
        outputs,
        images,
        strict=strict,
        issues=issues,
    )
    _validate_call_pairs(messages, strict=strict, issues=issues)
    if not any(message["role"] in {"assistant", "tool_call"} for message in messages):
        detail = "Responses context contains no trainable model output"
        if strict:
            raise ValueError(detail)
        issues.append({"type": "no_trainable_output", "detail": detail})

    return {
        "messages": messages,
        "tools": json.dumps(tools, ensure_ascii=False, separators=(",", ":")),
        "images": images,
    }


class _ProviderResponseFailed(ValueError):
    """Detect explicit provider failed/incomplete status, not malformed responses."""


def _parse_exchanges(
    exchanges: Iterable[dict[str, Any]],
    *,
    strict: bool = True,
    issues: list[dict[str, Any]] | None = None,
) -> tuple[
    list[dict[str, Any]],
    list[list[dict[str, Any]]],
    list[Any],
    str | None,
]:
    """Validate exchanges incrementally; retain only the last stateless context."""
    issues = issues if issues is not None else []
    requests: list[dict[str, Any]] = []
    outputs: list[list[dict[str, Any]]] = []
    signatures: list[str] = []
    baseline_tools: list[Any] = []
    baseline_index: int | None = None
    mode: str | None = None
    stop_reason: str | None = None
    saw_exchange = False

    for index, (exchange, following) in enumerate(iter_adjacent_exchanges(exchanges)):
        saw_exchange = True
        request = exchange.get("request")
        if not isinstance(request, dict):
            detail = f"Responses exchange {index} has no JSON request"
            if strict:
                raise ValueError(detail)
            issues.append({
                "type": "invalid_request",
                "exchange": index,
                "detail": detail,
            })
            continue
        if _is_context_compaction_request(request):
            issues.append({"type": "context_compaction", "exchange": index})
            continue

        response_items: list[dict[str, Any]] = []
        failure: str | None = None
        failure_cause: Exception | None = None
        if not exchange.get("complete") or exchange.get("status") != 200:
            failure = exchange_failure_detail(exchange, index)
        else:
            try:
                response_items = _response_items(
                    exchange.get("response", b""), index
                )
            except _ProviderResponseFailed as exc:
                failure = f"Responses exchange {index} failed; {exc}"
                failure_cause = exc
            except ValueError as exc:
                if strict:
                    raise
                issues.append({
                    "type": "invalid_provider_response",
                    "exchange": index,
                    "detail": str(exc),
                })
                continue

        if failure is not None:
            if is_immediate_retry(exchange, following, request):
                continue
            if following is not None:
                if strict:
                    raise ValueError(f"non-terminal {failure}") from failure_cause
                issues.append({
                    "type": "provider_exchange_failed",
                    "exchange": index,
                    "detail": failure,
                })
                continue
            stop_reason = failure

        snapshot = _request_tools(request)
        signature = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
        signatures.append(signature)
        if snapshot and baseline_index is None:
            baseline_index = len(signatures) - 1
            baseline_tools = snapshot

        if not requests:
            requests.append(request)
            outputs.append(response_items)
        else:
            chained = bool(request.get("previous_response_id"))
            mode = mode or ("chained" if chained else "stateless")
            if chained != (mode == "chained"):
                raise ValueError(
                    "Codex mixed chained and stateless Responses requests"
                )
            if mode == "chained":
                requests.append(request)
                outputs.append(response_items)
            else:
                # The later stateless request already contains the entire preceding context.
                requests[0], outputs[0] = request, response_items
        if failure is not None:
            break

    if not saw_exchange:
        raise ValueError("Codex made no recorded Responses API requests")
    if not requests:
        raise ValueError("Responses context has no recoverable agent requests")

    baseline_index = baseline_index or 0
    baseline = signatures[baseline_index]
    changed = [
        index for index, signature in enumerate(signatures)
        if signature != baseline
    ]
    if changed:
        detail = "Codex changed the provider tool schema during the solve"
        if strict:
            raise ValueError(detail)
        issues.append({
            "type": "tool_schema_changed",
            "baseline_request": baseline_index,
            "request_indices": changed,
        })
    return requests, outputs, baseline_tools, stop_reason


def _is_context_compaction_request(request: dict[str, Any]) -> bool:
    """Identify Codex compaction requests excluded from training context."""
    if _request_tools(request):
        return False
    value = request.get("input")
    if not isinstance(value, list):
        return False
    for item in reversed(value):
        if (
            isinstance(item, dict)
            and item.get("type") == "message"
            and item.get("role") == "user"
        ):
            return "CONTEXT CHECKPOINT COMPACTION" in _plain_text(item.get("content"))
    return False


def _response_items(response: bytes, exchange_index: int) -> list[dict[str, Any]]:
    # Responses may be JSON or SSE; normalize both to output items so downstream
    # message construction is independent of the transport format.
    text = response.decode("utf-8")
    try:
        body = json.loads(text)
    except ValueError:
        body = None
    if isinstance(body, dict):
        if body.get("error"):
            detail = response_error_detail(response) or "API error"
            raise _ProviderResponseFailed(detail)
        if body.get("status") not in {None, "completed"}:
            raise _ProviderResponseFailed(f"status={body.get('status')}")
        return [item for item in body.get("output", []) if isinstance(item, dict)]

    indexed: dict[int, dict[str, Any]] = {}
    completed = False
    failure_detail = ""
    data_lines: list[str] = []
    for line in text.splitlines() + [""]:
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
            continue
        if line or not data_lines:
            continue
        payload_text = "\n".join(data_lines)
        data_lines = []
        if payload_text == "[DONE]":
            continue
        try:
            event = json.loads(payload_text)
        except ValueError:
            continue
        event_type = event.get("type")
        if event_type == "response.output_item.done" and isinstance(event.get("item"), dict):
            indexed[int(event.get("output_index", len(indexed)))] = event["item"]
        elif event_type == "response.completed":
            completed = True
            if not indexed:
                response_obj = event.get("response") or {}
                for index, item in enumerate(response_obj.get("output", [])):
                    if isinstance(item, dict):
                        indexed[index] = item
        elif event_type in {"response.failed", "response.incomplete"}:
            response_obj = event.get("response") or {}
            error = response_obj.get("error") or {}
            code = error.get("code") if isinstance(error, dict) else None
            message = error.get("message") if isinstance(error, dict) else None
            failure_detail = ": ".join(str(value) for value in (code, message) if value)
            failure_detail = failure_detail or event_type
    if failure_detail:
        raise _ProviderResponseFailed(failure_detail)
    if not completed:
        raise ValueError(f"Responses exchange {exchange_index} has no completed response")
    return [indexed[index] for index in sorted(indexed)]


def _normalize_tools(snapshot: list[Any]) -> list[dict[str, Any]]:
    """Convert validated Responses tools into ms-swift format."""
    tools: list[dict[str, Any]] = []
    names: set[str] = set()
    for tool, namespace in _flatten_tools(snapshot):
        if not isinstance(tool, dict):
            raise ValueError("Responses request contains an invalid tool definition")
        tool_type = tool.get("type")
        builtin = _BUILTIN_TOOL_DEFINITIONS.get(str(tool_type))
        if builtin is not None:
            name = str(builtin["name"])
            function = {
                "name": name,
                "description": builtin["description"],
                "parameters": builtin["parameters"],
                "strict": False,
            }
            normalized = {"type": "function", "function": function}
        elif tool_type == "function" and isinstance(tool.get("function"), dict):
            function = dict(tool["function"])
            name = str(function.get("name") or "")
            normalized = {**tool, "function": function}
        elif not tool.get("name"):
            if tool_type:
                raise ValueError(
                    f"unsupported unnamed Responses tool type: {tool_type!r}"
                )
            raise ValueError("Responses request contains an unnamed tool definition")
        else:
            name = str(tool["name"])
            function = {
                "name": name,
                "description": tool.get("description", ""),
            }
            if tool_type == "function":
                function["parameters"] = (
                    tool.get("parameters")
                    or tool.get("inputSchema")
                    or tool.get("input_schema")
                    or {"type": "object"}
                )
            elif tool_type == "custom":
                function["parameters"] = {
                    "type": "object",
                    "properties": {"input": {"type": "string"}},
                    "required": ["input"],
                    "additionalProperties": False,
                }
            else:
                raise ValueError(f"unsupported Responses tool type: {tool_type!r}")
            function["strict"] = bool(tool.get("strict", False))
            normalized = {"type": "function", "function": function}

        if not name:
            raise ValueError("Responses request contains an unnamed tool definition")
        name = qualified_tool_name(name, namespace)
        function["name"] = name
        if name in names:
            raise ValueError(f"Responses request contains duplicate tool name: {name}")
        names.add(name)
        tools.append(normalized)
    return tools


def _request_tools(request: dict[str, Any]) -> list[Any]:
    tools: list[Any] = []
    seen: set[str] = set()

    def add(values: Any) -> None:
        for tool in values or []:
            try:
                marker = json.dumps(tool, sort_keys=True, separators=(",", ":"))
            except (TypeError, ValueError):
                marker = repr(tool)
            if marker not in seen:
                seen.add(marker)
                tools.append(tool)

    add(request.get("tools"))
    value = request.get("input")
    if not isinstance(value, list):
        return tools
    for item in value:
        if isinstance(item, dict) and item.get("type") == "additional_tools":
            add(item.get("tools"))
    return tools


def _flatten_tools(tools: list[Any], namespace: str | None = None):
    for tool in tools:
        if isinstance(tool, dict) and tool.get("type") == "namespace":
            yield from _flatten_tools(
                tool.get("tools") or [], qualified_tool_name(str(tool["name"]), namespace),
            )
            continue
        yield tool, namespace


def _build_messages(
    requests: list[dict[str, Any]],
    outputs: list[list[dict[str, Any]]],
    images: list[str],
    *,
    strict: bool = True,
    issues: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    # Stateless Responses requests carry either complete history or a
    # previous_response_id chain; never mix the two history representations.
    instructions = requests[-1].get("instructions")
    system_text = _plain_text(instructions)
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_text}]
    issues = issues if issues is not None else []

    chained = any(request.get("previous_response_id") for request in requests[1:])
    if chained:
        if any(not request.get("previous_response_id") for request in requests[1:]):
            raise ValueError("Codex mixed chained and stateless Responses requests")
        for request, response_items in zip(requests, outputs):
            _append_items(
                messages, request.get("input"), images,
                strict=strict, issues=issues,
            )
            _append_items(
                messages, response_items, images,
                strict=strict, issues=issues,
            )
    else:
        _append_items(
            messages, requests[-1].get("input"), images,
            strict=strict, issues=issues,
        )
        _append_items(
            messages, outputs[-1], images,
            strict=strict, issues=issues,
        )

    if not any(message["role"] == "user" for message in messages):
        raise ValueError("Responses context has no user message")
    if not messages[0]["content"]:
        raise ValueError("Responses context has no system or developer instructions")
    return messages


def _append_items(
    messages: list[dict[str, Any]],
    value: Any,
    images: list[str],
    *,
    strict: bool = True,
    issues: list[dict[str, Any]] | None = None,
) -> None:
    # Map training semantics here: merge public reasoning summaries into the following
    # assistant output; retain encrypted reasoning only in the raw wire trace.
    if isinstance(value, str):
        messages.append({"role": "user", "content": value})
        return
    if not isinstance(value, list):
        return

    issues = issues if issues is not None else []
    call_names = _known_call_names(messages)
    pending_reasoning: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "reasoning":
            summary = _plain_text(item.get("summary") or item.get("content")).strip()
            if summary:
                pending_reasoning.append(summary)
            continue

        if item_type == "message":
            role = item.get("role")
            if role != "assistant":
                _flush_reasoning(messages, pending_reasoning)
            if role in {"system", "developer"}:
                text, refs = _content(item.get("content"))
                if refs:
                    raise ValueError("system instructions contain unsupported images")
                if text:
                    existing = messages[0]["content"]
                    messages[0]["content"] = (
                        f"{existing}\n\n{text}" if existing else text
                    )
                continue
            if role not in {"user", "assistant"}:
                continue
            content, refs = _content(item.get("content"))
            images.extend(refs)
            if role == "assistant":
                content = _take_reasoning(pending_reasoning, content)
            if content:
                messages.append({"role": role, "content": content})
        elif item_type in {"function_call", "custom_tool_call"}:
            _flush_reasoning(messages, pending_reasoning)
            name = qualified_tool_name(str(item.get("name") or ""), item.get("namespace"))
            call_id = str(item.get("call_id") or "")
            arguments = {"input": item.get("input")} if item_type == "custom_tool_call" else item.get("arguments")
            if item_type == "function_call" and isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except ValueError:
                    pass
            messages.append({
                "role": "tool_call",
                "content": json.dumps(
                    {"name": name, "arguments": arguments},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "_call_id": call_id,
            })
            call_names[call_id] = name
        elif item_type in {"function_call_output", "custom_tool_call_output"}:
            _flush_reasoning(messages, pending_reasoning)
            call_id = str(item.get("call_id") or "")
            name = call_names.get(call_id, "")
            content, refs = _content(
                item.get("output"), image_placeholders=False
            )
            images.extend(refs)
            response = {"name": name, "content": content}
            if refs:
                response["images"] = "<image>" * len(refs)
            messages.append({
                "role": "tool_response",
                "content": json.dumps(response, ensure_ascii=False, separators=(",", ":")),
                "_call_id": call_id,
            })
        elif isinstance(item_type, str) and item_type.endswith("_call"):
            detail = f"Responses built-in tool call {item_type!r} has no SFT mapping"
            if strict:
                raise ValueError(detail)
            issues.append({
                "type": "unsupported_builtin_tool_call",
                "tool_type": item_type,
                "detail": detail,
            })
    _flush_reasoning(messages, pending_reasoning)


def _take_reasoning(reasoning: list[str], content: str = "") -> str:
    if not reasoning:
        return content
    summary = "\n\n".join(reasoning)
    reasoning.clear()
    think = f"<think>\n{summary}\n</think>"
    return f"{think}\n\n{content}" if content else think


def _flush_reasoning(
    messages: list[dict[str, Any]],
    reasoning: list[str],
) -> None:
    if content := _take_reasoning(reasoning):
        messages.append({"role": "assistant", "content": content})


def _known_call_names(messages: list[dict[str, Any]]) -> dict[str, str]:
    names: dict[str, str] = {}
    for message in messages:
        if message.get("role") != "tool_call":
            continue
        call_id = message.get("_call_id")
        if call_id:
            names[str(call_id)] = str(json.loads(message["content"]).get("name") or "")
    return names


def _content(
    value: Any,
    *,
    image_placeholders: bool = True,
) -> tuple[str, list[str]]:
    # Inline text and retain archived image references. Tool responses keep image
    # placeholders while sample.images holds the actual image references.
    if isinstance(value, str):
        return value, []
    if isinstance(value, dict):
        if not value.get("type"):
            return json.dumps(value, ensure_ascii=False, default=str), []
        value = [value]
    if not isinstance(value, list):
        return json.dumps(value, ensure_ascii=False, default=str), []

    parts: list[str] = []
    refs: list[str] = []
    for block in value:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type in {"input_text", "output_text", "text"}:
            text = str(block.get("text") or "")
            if text:
                parts.append(text)
        elif block_type in {"input_image", "image", "image_url"}:
            refs.append(_image_path(block))
            if image_placeholders:
                parts.append("<image>")
    return "\n".join(parts), refs


def _image_path(block: dict[str, Any]) -> str:
    reference = block.get("image_url") or block.get("url")
    if isinstance(reference, dict):
        reference = reference.get("url", reference)
    if not isinstance(reference, dict) or not isinstance(reference.get(IMAGE_REF_KEY), str):
        raise ValueError("provider image must reference the session image archive")
    path = reference[IMAGE_REF_KEY]
    parts = PurePosixPath(path).parts
    if (len(parts) != 2 or parts[0] != "images" or "\\" in path
            or ".." in parts or PurePosixPath(path).as_posix() != path):
        raise ValueError("provider image reference must be relative to images/")
    if reference.get("media_type") not in {"image/png", "image/jpeg", "image/webp"}:
        raise ValueError(f"unsupported provider image MIME type: {reference.get('media_type')}")
    return path


def _plain_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        value = [value]
    if isinstance(value, list):
        return "\n".join(
            str(part.get("text") or "")
            for part in value
            if isinstance(part, dict) and part.get("text")
        )
    return ""


def _validate_call_pairs(
    messages: list[dict[str, Any]],
    *,
    strict: bool = True,
    issues: list[dict[str, Any]] | None = None,
) -> None:
    # ms-swift tool_call/tool_response pairs must share call_id; reject incomplete
    # history rather than exporting samples whose calls cannot be aligned.
    issues = issues if issues is not None else []
    calls: dict[str, int] = {}
    outputs: dict[str, int] = {}
    for message in messages:
        call_id = message.pop("_call_id", None)
        if message["role"] == "tool_call":
            if not call_id:
                detail = "provider tool call has no call_id"
                if strict:
                    raise ValueError(detail)
                issues.append({"type": "missing_call_id", "role": "tool_call"})
                continue
            calls[call_id] = calls.get(call_id, 0) + 1
        elif message["role"] == "tool_response":
            if not call_id:
                detail = "provider tool response has no call_id"
                if strict:
                    raise ValueError(detail)
                issues.append({"type": "missing_call_id", "role": "tool_response"})
                continue
            outputs[call_id] = outputs.get(call_id, 0) + 1
    if calls != outputs:
        detail = "provider context contains unmatched tool calls or responses"
        if strict:
            raise ValueError(detail)
        mismatches = {
            call_id: {
                "calls": calls.get(call_id, 0),
                "responses": outputs.get(call_id, 0),
            }
            for call_id in sorted(set(calls) | set(outputs))
            if calls.get(call_id, 0) != outputs.get(call_id, 0)
        }
        issues.append({
            "type": "unmatched_tool_calls",
            "counts": mismatches,
        })


from uni_robot_agent.events import ErrorEvent
from uni_robot_agent.telemetry.logging import get_logger
import time
logger = get_logger("session")

def finalize_model_records(gateway, result, session_dir, event_bus) -> None:
    """Diagnose frozen exchanges and export the format declared by the agent."""
    import traceback
    from uni_robot_agent.provider.recording.exchanges import get_model_exchange_error

    records = gateway.trace_records
    main_records = [
        item for item in records if item.get("thread_id") == result.main_thread_id
    ]
    result.provider_error = get_model_exchange_error(
        main_records,
        interrupted=result.stop_reason in {"max_turns", "wall_time_limit"},
    )
    if result.provider_error:
        if event_bus is not None:
            event_bus.emit(ErrorEvent(message=result.provider_error))
        result.error = result.error or result.provider_error
    train_traceback = None
    if session_dir is not None:
        result.provider_log_path = str(Path(session_dir) / "codex_responses")
        if result.trace_format == "codex_responses":
            try:
                export = write_codex_sft_sample(
                    gateway.iter_training_exchanges(thread_id=result.main_thread_id),
                    session_dir,
                )
                result.train_path = str(export.path)
                result.train_partial = export.partial
                result.train_stop_reason = export.stop_reason
            except Exception as exc:
                result.train_error = f"{type(exc).__name__}: {exc}"
                train_traceback = traceback.format_exc()
                try:
                    result.train_debug_path = str(
                        write_codex_sft_debug_sample(
                            gateway.iter_training_exchanges(
                                thread_id=result.main_thread_id
                            ),
                            session_dir,
                            result.train_error,
                        )
                    )
                except Exception as debug_exc:
                    logger.error("Debug trajectory save failed: %s", debug_exc)
    if result.train_path and not result.train_error and not result.train_partial:
        gateway.release_training_payloads(thread_id=result.main_thread_id)
        records = gateway.trace_records
    gateway.persist_report(
        {
            "provider_error": result.provider_error,
            "agent_error": result.error,
            "agent_traceback": result.traceback,
            "exchange_count": len(records),
            "exchange_persistence_errors": [
                {"index": item.get("index", index), "error": item["persistence_error"]}
                for index, item in enumerate(records)
                if item.get("persistence_error")
            ],
            "train_path": str(Path(result.train_path).relative_to(session_dir)) if result.train_path else None,
            "train_partial": result.train_partial,
            "train_stop_reason": result.train_stop_reason,
            "train_error": result.train_error,
            "train_traceback": train_traceback,
            "train_debug_path": str(Path(result.train_debug_path).relative_to(session_dir)) if result.train_debug_path else None,
            "generated_at": time.time(),
        }
    )
