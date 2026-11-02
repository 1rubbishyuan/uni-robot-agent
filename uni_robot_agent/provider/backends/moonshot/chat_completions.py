"""Kimi Chat Completions settings on the shared protocol codec."""

from __future__ import annotations

from typing import Any

from uni_robot_agent.provider.conversions import responses_chat
from uni_robot_agent.provider.contracts import DecodedResponse, EncodedRequest

# Kimi K3 has low/high/max, so intermediate SDK efforts map to the nearest supported level.
_EFFORT_LEVELS = {
    "xhigh": "max", "max": "max", "high": "high", "medium": "high",
    "low": "low", "xlow": "low", "minimal": "low", "none": "low",
}


class MoonshotChatCompletionsAdapter:
    profile_name = "moonshot.chat_completions"

    def target_url(self, upstream_base_url: str, request_path: str) -> str:
        return responses_chat.target_url(upstream_base_url, request_path)

    def encode_request(self, request: dict[str, Any]) -> EncodedRequest:
        encoded = responses_chat.encode_request(request, native_search_tool={
            "type": "builtin_function", "function": {"name": "$web_search"},
        })
        reasoning = request.get("reasoning")
        effort = reasoning.get("effort") if isinstance(reasoning, dict) else None
        if isinstance(effort, str) and effort:
            encoded.body["reasoning_effort"] = _EFFORT_LEVELS.get(effort, effort)
        return encoded

    def decode_response(self, response: bytes, decode_state: dict[str, Any]) -> DecodedResponse:
        return responses_chat.decode_response(response, decode_state)
