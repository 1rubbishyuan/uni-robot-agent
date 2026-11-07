"""Small synchronous client for the session's existing MCP transport."""
from __future__ import annotations

import base64
from dataclasses import dataclass
from functools import cached_property, partial
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx

from uni_robot_agent.tools.presentation import receipt_summary
from uni_robot_agent.tools.results import MotionFeedback, ResultFields, ResultState


@dataclass(frozen=True)
class CodeResult:
    """Original payload/images with optional typed feedback and explicit summary.

    Treat data as a receipt, not mutable robot state. Typed views are constructed
    lazily; neither inspecting them nor summarizing them performs robot calls.
    """

    data: dict[str, Any]
    images: tuple[Path, ...]
    call_id: str

    @cached_property
    def _fields(self) -> ResultFields:
        return ResultFields.model_validate(self.data)

    @property
    def call_ok(self) -> bool | None:
        return self._fields.call_ok

    @property
    def error(self) -> str | None:
        return self._fields.error

    @property
    def remaining_steps(self) -> int | None:
        return self._fields.remaining_steps

    @property
    def snapshot_id(self) -> str | None:
        return self._fields.snapshot_id

    @property
    def artifact_error(self) -> str | None:
        return self._fields.artifact_error

    @property
    def state(self) -> ResultState | None:
        return self._fields.state

    @property
    def feedback(self) -> MotionFeedback | None:
        return self._fields.feedback

    def summary(self) -> str:
        """Return compact receipt JSON for explicit printing; open images separately."""
        return receipt_summary(self._fields, data=self.data, images=self.images, call_id=self.call_id)


class ToolCallError(RuntimeError):
    """A failed tool call; inspect result without automatically replaying it."""

    def __init__(self, result: CodeResult):
        super().__init__(str(result.data))
        self.result = result


class Robot:
    """Connect to the current episode; each call waits for completed feedback.

    Use as a context manager. Variables belong to this Python process, while the
    robot episode and scratch files belong to the outer session.
    """

    def __init__(self, url: str | None = None, *, image_dir: str | Path | None = None):
        self._url = url or os.environ["UNI_ROBOT_AGENT_CODE_URL"]
        self._image_dir = Path(image_dir or os.environ["UNI_ROBOT_AGENT_CODE_IMAGES"])
        self._http = httpx.Client(timeout=None, trust_env=False)
        self._closed = False
        try:
            self._request("initialize", {})
            self.schemas = self._request("tools/list", {})["tools"]
            self.tools = SimpleNamespace(**{
                spec["name"]: partial(self.call, spec["name"]) for spec in self.schemas
            })
        except BaseException:
            self.close()
            raise

    def __enter__(self) -> Robot:
        return self

    def __exit__(self, *_):
        self.close()

    def close(self) -> None:
        self._closed = True
        self._http.close()

    def _request(self, method: str, params: dict) -> dict:
        if self._closed:
            raise RuntimeError("Robot client is closed; do not replay an uncertain action")
        request_id = uuid4().hex
        try:
            response = self._http.post(self._url, json={
                "jsonrpc": "2.0", "id": request_id, "method": method, "params": params,
            }, headers={"Accept": "application/json"})
            response.raise_for_status()
            body = response.json()
            if body.get("id") != request_id or "result" not in body:
                raise ValueError(f"Invalid MCP response: {body}")
            return body["result"]
        except (httpx.HTTPError, ValueError) as error:
            self.close()
            raise RuntimeError(
                "Robot connection failed. Execution may have occurred; do not replay "
                "the request or continue control. Report the uncertainty to the session owner."
            ) from error

    def call(self, name: str, **arguments: Any) -> CodeResult:
        """Call an exact declared tool name with its unchanged keyword arguments."""
        if name not in {spec["name"] for spec in self.schemas}:
            raise ValueError(f"Undeclared robot tool: {name}")
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        images, data = [], {}
        for block in result["content"]:
            if block["type"] == "image":
                raw = base64.b64decode(block["data"], validate=True)
                suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(
                    block["mimeType"], ".img")
                self._image_dir.mkdir(parents=True, exist_ok=True)
                path = self._image_dir / (hashlib.sha256(raw).hexdigest() + suffix)
                path.write_bytes(raw)
                images.append(path.resolve())
            elif block["type"] == "text":
                try:
                    data.update(json.loads(block["text"]))
                except (ValueError, TypeError):
                    data["error"] = block["text"]
        output = CodeResult(data, tuple(images), result.get("_meta", {}).get("uni_robot_agent/call_id", ""))
        if result.get("isError"):
            raise ToolCallError(output)
        return output
