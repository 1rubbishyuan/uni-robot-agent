"""JSON data events shared by Responses and Chat Completions streams."""
from __future__ import annotations
import json
from typing import Any

def decode_events(
    response: bytes,
) -> tuple[list[dict[str, Any]], bool] | None:
    events: list[dict[str, Any]] = []
    has_done = False
    for line in response.decode("utf-8", errors="replace").splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            has_done = True
            continue
        if not payload:
            continue
        try:
            event = json.loads(payload)
        except ValueError:
            return None
        if not isinstance(event, dict):
            return None
        events.append(event)
    return (events, has_done) if events else None


def encode_events(events: list[dict[str, Any]], *, has_done: bool) -> bytes:
    body = b"".join(
        f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode()
        for event in events
    )
    return body + (b"data: [DONE]\n\n" if has_done else b"")
