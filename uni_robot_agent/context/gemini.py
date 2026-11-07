"""Atomic history blocks and tool-image retention for native GenerateContent."""

from __future__ import annotations

import copy
from typing import Any, Iterator

from .history import ContextBlock, ContextRequest


def _is_image(part: dict[str, Any]) -> bool:
    return str(part.get("inlineData", {}).get("mimeType", "")).startswith("image/")


def _tool_images(content: dict[str, Any]) -> Iterator[tuple[list, int, dict]]:
    """Yield only attachments with an explicit function-response provenance."""
    response = None
    parts = content.get("parts", [])
    for index, part in enumerate(parts):
        if "functionResponse" in part:
            response = part["functionResponse"]
            attachments = response.get("parts", [])
            for nested_index, attachment in enumerate(attachments):
                if _is_image(attachment):
                    yield attachments, nested_index, response
        elif response is not None and _is_image(part):
            yield parts, index, response


def build_blocks(items: tuple[Any, ...]) -> list[ContextBlock]:
    """Keep every call batch and all result contents together; pin user input."""
    blocks: list[ContextBlock] = []
    current: list[tuple[int, dict]] = []
    pending: dict[str, str] = {}
    seen: set[str] = set()

    def flush(kind: str) -> None:
        values = tuple(item for _, item in current)
        # An image before any response has no tool provenance and must survive.
        unowned_image = user_text = False
        for item in values:
            has_response = False
            for part in item.get("parts", []):
                has_response |= "functionResponse" in part
                unowned_image |= _is_image(part) and not has_response
                user_text |= item.get("role", "user") == "user" and "text" in part
        blocks.append(ContextBlock(
            item_ids=tuple(f"item_{index:04d}" for index, _ in current),
            items=values, kind=kind, pinned=kind == "user" or unowned_image or user_text,
            observation=any(list(_tool_images(item)) for item in values),
        ))
        current.clear()

    for index, item in enumerate(items):
        if not isinstance(item, dict) or not isinstance(item.get("parts"), list):
            raise ValueError("Gemini context requires content objects with parts")
        role = item.get("role", "user")
        if role not in {"user", "model"}:
            raise ValueError("Unsupported Gemini context role")
        if role == "model" and pending:
            raise ValueError("Model content precedes outstanding Gemini tool results")
        has_call = has_response = False
        for part in item["parts"]:
            if not isinstance(part, dict):
                raise ValueError("Gemini context requires part objects")
            if "functionCall" in part:
                call = part["functionCall"]
                key, name = call.get("id"), call.get("name")
                if (role != "model" or not isinstance(key, str) or not key
                        or key in seen or not isinstance(name, str) or not name):
                    raise ValueError("Gemini function call requires unique ID and name")
                pending[key] = name
                seen.add(key)
                has_call = True
            if "functionResponse" in part:
                response = part["functionResponse"]
                key = response.get("id")
                if (role != "user" or not isinstance(key, str) or key not in pending
                        or pending[key] != response.get("name")):
                    raise ValueError("Gemini function response has no matching call ID/name")
                del pending[key]
                has_response = True
        if pending and not (has_call or has_response):
            raise ValueError("User input interrupts outstanding Gemini tool results")
        current.append((index, item))
        if not pending:
            flush("tool_turn" if has_response else "user" if role == "user" else "assistant_turn")
    if pending:
        raise ValueError("Gemini history contains tool calls without matching results")
    return blocks


def evict_old_images(
    request: ContextRequest, *, keep_n: int, batch_size: int,
) -> tuple[ContextRequest, int]:
    image_count = sum(len(list(_tool_images(item))) for item in request.items)
    evict_count = max(0, image_count - keep_n) // batch_size * batch_size
    if not evict_count:
        return request, 0

    items = copy.deepcopy(list(request.items))
    remaining = evict_count
    for item in items:
        # Snapshot locations before removing nested attachments. Rebuild those
        # lists afterwards so image indices and call IDs stay stable.
        removals: dict[int, tuple[list, set[int]]] = {}
        placeholders = []
        image_index = 0
        for parts, index, response in _tool_images(item):
            if not remaining:
                break
            image_index += 1
            placeholder = {"text": (
                f"[image omitted: tool={response['name']}, call_id={response['id']}, "
                f"image={image_index}]"
            )}
            if parts is item["parts"]:
                parts[index] = placeholder
            else:
                # FunctionResponse.parts accepts media, not arbitrary text.
                # Keep response JSON intact and put the notice in user.parts.
                removals.setdefault(id(parts), (parts, set()))[1].add(index)
                placeholders.append(placeholder)
            remaining -= 1
        for parts, indices in removals.values():
            parts[:] = [part for index, part in enumerate(parts) if index not in indices]
        item["parts"].extend(placeholders)
        if not remaining:
            break
    return request.with_items(items), evict_count - remaining
