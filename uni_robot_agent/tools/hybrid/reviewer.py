"""Review transport, independent of robot geometry and physical execution."""
from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import httpx
from jsonschema.exceptions import ValidationError

from uni_robot_agent.provider import ModelClient, build_provider_adapter

from .decisions import decision_schema


class ReviewerError(RuntimeError):
    """Review failed before any proposed motion was authorized."""


class ChunkReviewer:
    """Review action proposals without executing or retrying physical actions."""

    def __init__(self, *, enabled, model, prompt_path, validator,
                 base_url=None, api_key=None, adapter="passthrough.responses", tracer=None):
        self.adapter = adapter
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

    def decide(self, *, context, images, actions_per_chunk, record=None):
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
        request = {"model": self.model, "reasoning": {"effort": "medium"},
            "tools": [{"type": "function", "name": "submit_review",
                       "description": "Submit exactly one review decision for the current proposal.", "strict": False,
                       "parameters": decision_schema(context, actions_per_chunk)}],
            "tool_choice": {"type": "function", "name": "submit_review"},
            "parallel_tool_calls": False, "input": [
                {"role": "system", "content": [{"type": "input_text", "text": self.prompt}]},
                {"role": "user", "content": content}]}
        provider_attempts = 0
        started = time.monotonic()
        for attempt in range(2):
            received = []
            attempt_started = time.monotonic()
            evidence = {"request_id": context["request_id"], "state_version": context.get("state_version"),
                "model": self.model, "adapter": self.adapter, "reasoning_effort": "medium",
                "started_wall_ns": time.time_ns(), "repair": attempt > 0}
            try:
                reply = self._client.request(request,
                    headers={"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"},
                    on_response=received.append)
                response = json.loads(reply.body)
                decision = self.validator(review_arguments(response), context, actions_per_chunk)
            except (ValueError, TypeError, ValidationError) as error:
                rejected = f"{type(error).__name__}: {error}"
                evidence["error"] = {"stage": "decision", "message": rejected}
            except Exception as error:
                evidence["error"] = {"stage": "transport", "message": f"{type(error).__name__}: {error}"}
                raise ReviewerError(evidence["error"]["message"]) from error
            else:
                decision.update(model=self.model, reasoning_effort="medium",
                    provider_attempts=provider_attempts + len(reply.attempts), review_attempts=attempt + 1,
                    elapsed_s=round(time.monotonic() - started, 3), usage=response.get("usage"))
                evidence["decision"] = decision
                return decision
            finally:
                evidence["elapsed_s"] = round(time.monotonic() - attempt_started, 3)
                if received:
                    raw = received[-1]
                    provider_attempts += len(raw.attempts)
                    evidence.update(status=raw.status, provider_attempts=list(raw.attempts),
                                    raw_response=raw.body)
                if record is not None:
                    record(attempt, evidence)
            if attempt == 0:
                # Reassess the same immutable proposal; never predict or execute on repair.
                request["input"].append({"role": "user", "content": [{"type": "input_text", "text":
                    "The previous review was rejected: " + rejected +
                    ". Call submit_review exactly once using its schema for the SAME request_id and proposal. "
                    "episode_started describes the whole native episode, not the local phase."}]})
        raise ReviewerError("Decision rejected after two review attempts: " + rejected)


def review_arguments(response):
    """Accept one explicit decision call; reasoning and prose cannot authorize motion."""
    if not isinstance(response, dict):
        raise TypeError("Reviewer response must be an object")
    if response.get("status") != "completed":
        raise ValueError("Reviewer response did not complete")
    output = response.get("output", [])
    if not isinstance(output, list) or not all(isinstance(item, dict) for item in output):
        raise TypeError("Reviewer output must be a list of response items")
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
