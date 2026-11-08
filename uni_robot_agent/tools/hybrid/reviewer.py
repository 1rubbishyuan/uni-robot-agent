"""Review transport, independent of robot geometry and physical execution."""
from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import httpx

from uni_robot_agent.provider import ModelClient, build_provider_adapter

from .decisions import decision_schema


class ChunkReviewer:
    """Review action proposals without executing or retrying physical actions."""

    def __init__(self, *, enabled, model, prompt_path, validator,
                 base_url=None, api_key=None, adapter="passthrough.responses", tracer=None):
        self.enabled, self.model = enabled, model
        self.base_url, self.api_key, self.validator = base_url, api_key, validator
        self.prompt = Path(prompt_path).read_text() if enabled else ""
        self._client = ModelClient(base_url or "", adapter=build_provider_adapter(adapter, client_protocol="responses"),
            request_timeout_s=450, max_retries=1, retry_backoff_initial_s=2,
            retry_http_errors=False, retry_response_errors=False,
            retryable_exceptions=(httpx.TransportError, TimeoutError, ConnectionError),
            tracer=tracer) if enabled else None

    def close(self):
        if self._client is not None:
            self._client.close()

    def decide(self, *, context, images, actions_per_chunk):
        if not self.enabled:
            return {"request_id": context["request_id"], "mode": "student", "steps": actions_per_chunk,
                    "assessment": None, "continuation": "continue", "reason": "Action model without review.", "controller_disabled": True}
        if not self.base_url or not self.api_key:
            raise ValueError("Reviewer endpoint and credential are required")
        content = [{"type": "input_text", "text": json.dumps(context, ensure_ascii=False, allow_nan=False)}]
        for name, path in images.items():
            encoded = base64.b64encode(Path(path).read_bytes()).decode("ascii")
            content.extend([{"type": "input_text", "text": "Observation: " + name},
                            {"type": "input_image", "image_url": "data:image/png;base64," + encoded}])
        started = time.monotonic()
        reply = self._client.request({"model": self.model, "reasoning": {"effort": "medium"},
            "tools": [{"type": "function", "name": "submit_review",
                       "description": "Submit exactly one review decision for the current proposal.",
                       "parameters": decision_schema(context, actions_per_chunk)}],
            "tool_choice": {"type": "function", "name": "submit_review"},
            "parallel_tool_calls": False, "input": [
            {"role": "system", "content": [{"type": "input_text", "text": self.prompt}]},
            {"role": "user", "content": content}]},
            headers={"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"})
        response = json.loads(reply.body)
        decision = self.validator(review_arguments(response), context, actions_per_chunk)
        decision.update(model=self.model, reasoning_effort="medium", provider_attempts=len(reply.attempts),
                        elapsed_s=round(time.monotonic() - started, 3), usage=response.get("usage"))
        return decision


def review_arguments(response):
    """Accept one explicit decision call; reasoning and prose cannot authorize motion."""
    if response.get("status") != "completed":
        raise ValueError("Reviewer response did not complete")
    output = response.get("output", [])
    calls = [item for item in output if item.get("type") == "function_call"]
    if (len(calls) != 1 or calls[0].get("name") != "submit_review"
            or any(item.get("type") not in {"function_call", "reasoning"} for item in output)):
        raise ValueError("Reviewer must return exactly one submit_review function call")
    arguments = calls[0].get("arguments")
    if not isinstance(arguments, str):
        raise TypeError("Reviewer function arguments must be a JSON string")
    decision = json.loads(arguments)
    if not isinstance(decision, dict):
        raise TypeError("Reviewer function arguments must contain one JSON object")
    return decision
