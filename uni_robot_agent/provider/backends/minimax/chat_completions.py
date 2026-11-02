"""MiniMax Chat settings on the shared Responses/Chat codec."""

from __future__ import annotations

from typing import Any

from uni_robot_agent.provider.conversions import responses_chat
from uni_robot_agent.provider.contracts import DecodedResponse, EncodedRequest
from uni_robot_agent.provider.image_encoding import lossless_image_url


class MiniMaxChatCompletionsAdapter:
    profile_name = "minimax.chat_completions"

    def target_url(self, upstream_base_url: str, request_path: str) -> str:
        return responses_chat.target_url(upstream_base_url, request_path)

    def encode_request(self, request: dict[str, Any]) -> EncodedRequest:
        encoded = responses_chat.encode_request(request)
        body = encoded.body
        effort = (request.get("reasoning") or {}).get("effort", "max")
        if effort not in {"low", "medium", "high", "xhigh", "max"}:
            raise ValueError("MiniMax requires reasoning effort low, medium, high, xhigh, or max")
        body.update(reasoning_effort=effort, reasoning_split=True)
        if "max_tokens" in body:
            body["max_completion_tokens"] = body.pop("max_tokens")
        if body["stream"]:
            body["stream_options"] = {"include_usage": True}
        for message in body["messages"]:
            if not isinstance(message.get("content"), list):
                continue
            for part in message["content"]:
                if part.get("type") == "image_url":
                    image = dict(part["image_url"])
                    image["url"] = lossless_image_url(image["url"])
                    if image.get("detail") == "auto":
                        image["detail"] = "default"
                    part["image_url"] = image
        return encoded

    def decode_response(self, response: bytes, decode_state: dict[str, Any]) -> DecodedResponse:
        return responses_chat.decode_response(response, decode_state)
