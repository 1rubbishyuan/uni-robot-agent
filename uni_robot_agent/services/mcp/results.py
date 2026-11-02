"""Encode execution outcomes independently of MCP transport and event ownership."""

from __future__ import annotations

import base64
import json
from typing import Any

from uni_robot_agent.execution import ExecutionResult


def encode_call_result(result: ExecutionResult) -> dict[str, Any]:
    """Preserve tool payloads or accepted job handles in a complete MCP result."""
    output = result.output
    content: list[dict[str, Any]] = []
    if output is not None:
        content.extend(
            {
                "type": "image",
                "data": base64.b64encode(image.data).decode("ascii"),
                "mimeType": image.mime_type,
            }
            for image in output.images
        )
    payload = output.to_dict(for_model=True) if output is not None else result.to_dict()
    content.append({
        "type": "text",
        "text": json.dumps(payload, ensure_ascii=False, default=str),
    })
    # Domain outcomes live in extra; only call validity controls MCP failure.
    is_error = output is not None and (not output.success or bool(output.error))
    return {"content": content, "isError": is_error}


def encode_call_error(error: Exception) -> dict[str, Any]:
    """Report ordinary tool execution errors without changing transport control flow."""
    return {
        "content": [{"type": "text", "text": f"Error: {error}"}],
        "isError": True,
    }
