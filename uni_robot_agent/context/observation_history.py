"""Observation ownership in native histories, separate from window selection."""
from dataclasses import dataclass
import json
from typing import Any, Callable


def _is_image(value):
    return isinstance(value, dict) and (
        value.get("type") in ("input_image", "output_image", "image", "image_url")
        or str(value.get("inlineData", {}).get("mimeType", "")).startswith("image/"))


def estimate_context(body, *, tokens_per_image=1536):
    """Heuristic text + per-image allowance, never base64 length as image tokens."""
    images = 0

    def text_only(value):
        nonlocal images
        if _is_image(value):
            images += 1
            return "[image]"
        if isinstance(value, dict):
            return {key: text_only(item) for key, item in value.items()}
        if isinstance(value, list):
            return [text_only(item) for item in value]
        return value

    text = json.dumps(text_only(body), ensure_ascii=False, separators=(",", ":"), default=str)
    return {"text": max(1, (len(text.encode()) + 2)//3),
            "images": images*tokens_per_image, "image_count": images}


@dataclass
class Observation:
    payload: dict[str, Any]
    save: Callable[[], None]
    images: list[tuple[list, dict]]
    call_id: str
    item_index: int
    pinned: bool = False

    @property
    def metadata(self):
        return self.payload.get("observation", self.payload)

    @property
    def key(self):
        return str(self.payload.get("snapshot_id") or self.metadata.get("artifact_ref")
                   or self.metadata.get("observation_path") or self.call_id)

    def evict(self, *, compact_metadata):
        # Remove attachments by identity: parallel calls can share the same parts list.
        for parts, image in self.images:
            parts[:] = [part for part in parts if part is not image]
        metadata = self.metadata
        names = list(metadata.get("image_cameras", []))
        camera_key = "cameras" if "observation" in self.payload else "camera_calibration"
        if compact_metadata:
            metadata.pop(camera_key, None)
        else:
            for camera in metadata.get(camera_key, {}).values():
                for key in ("intrinsic_K", "T_world_from_optical", "image_path"):
                    camera.pop(key, None)
        metadata.update(images_omitted=True, image_cameras=[], observed_cameras=names)
        self.save()


def _payload_slots(owner, key):
    """Decode structured results only; never scrape JSON from arbitrary prose."""
    value = owner.get(key)
    if isinstance(value, str):
        try:
            payload = json.loads(value)
        except ValueError:
            return []
        if isinstance(payload, dict):
            return [(payload, lambda: owner.__setitem__(key, json.dumps(
                payload, ensure_ascii=False, separators=(",", ":"))))]
        return []
    if isinstance(value, dict):
        # GenerateContent adapters can wrap the structured tool output in result.
        payload = value.get("result", value)
        return [(payload, lambda: None)] if isinstance(payload, dict) else []
    if isinstance(value, list):
        return [slot for part in value if isinstance(part, dict) and "text" in part
                for slot in _payload_slots(part, "text")]
    return []


def _observation(slots, images, call_id, index):
    matches = []
    for payload, save in slots:
        metadata = payload.get("observation", payload)
        cameras = metadata.get("cameras" if "observation" in payload else "camera_calibration")
        names = metadata.get("image_cameras")
        if (isinstance(cameras, dict) and isinstance(names, list) and names
                and len(names) == len(images) and set(names).issubset(cameras)):
            matches.append(Observation(payload, save, images, call_id, index,
                                       pinned=metadata.get("retain_in_context") is True))
    # Ambiguous ownership is kept unchanged, rather than guessing an image mapping.
    return matches[0] if len(matches) == 1 else None


def _responses_events(items):
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind in {"function_call", "custom_tool_call"}:
            yield "call", item.get("call_id"), item.get("name"), index, [], []
        elif kind in {"function_call_output", "custom_tool_call_output"}:
            parts = item.get("output")
            images = [(parts, part) for part in parts if _is_image(part)] if isinstance(parts, list) else []
            yield "result", item.get("call_id"), None, index, _payload_slots(item, "output"), images


def _gemini_events(items):
    for index, item in enumerate(items):
        parts = item.get("parts", [])
        for position, part in enumerate(parts):
            if "functionCall" in part:
                call = part["functionCall"]
                yield "call", call.get("id"), call.get("name"), index, [], []
            if "functionResponse" in part:
                response = part["functionResponse"]
                attachments = response.get("parts", [])
                images = [(attachments, image) for image in attachments if _is_image(image)]
                for following in parts[position+1:]:
                    if "functionResponse" in following or "functionCall" in following:
                        break
                    if _is_image(following):
                        images.append((parts, following))
                yield "result", response.get("id"), response.get("name"), index, _payload_slots(response, "response"), images


_EVENTS = {"responses": _responses_events, "gemini": _gemini_events}


def observations(items, wire_format):
    try:
        events = _EVENTS[wire_format]
    except KeyError:
        raise ValueError(f"observation_window does not support {wire_format}") from None
    pending, seen, batch, result = {}, set(), [], []
    for kind, call_id, name, index, slots, images in events(items):
        if not isinstance(call_id, str) or not call_id:
            raise ValueError("Tool history requires explicit call IDs")
        if kind == "call":
            if call_id in seen:
                raise ValueError(f"Duplicate tool call ID: {call_id}")
            seen.add(call_id)
            pending[call_id] = name
            continue
        if call_id not in pending or (name is not None and name != pending[call_id]):
            raise ValueError(f"Tool result has no matching call: {call_id}")
        del pending[call_id]
        view = _observation(slots, images, call_id, index)
        if view is not None:
            batch.append(view)
            result.append(view)
        if not pending:
            batch.clear()
    # Preserve completed results belonging to a still-open parallel call batch.
    for view in batch:
        view.pinned = True
    return result
