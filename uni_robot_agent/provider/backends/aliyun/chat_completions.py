"""Qwen Chat settings and lossless image encoding on the shared protocol codec."""

from __future__ import annotations

from typing import Any

from uni_robot_agent.provider.conversions import responses_chat
from uni_robot_agent.provider.contracts import DecodedResponse, EncodedRequest
from uni_robot_agent.provider.image_encoding import lossless_image_url


class AliyunChatCompletionsAdapter:
    profile_name = "aliyun.chat_completions"

    def __init__(self, *, thinking=True):
        self.thinking = thinking

    def target_url(self, upstream_base_url: str, request_path: str) -> str:
        return responses_chat.target_url(upstream_base_url, request_path)

    def encode_request(self, request: dict[str, Any]) -> EncodedRequest:
        encoded = responses_chat.encode_request(request)
        body = encoded.body
        body.update(enable_thinking=self.thinking,
                    reasoning_effort=(request.get("reasoning") or {}).get("effort", "max"),
                    max_tokens=65536 if self.thinking else 8192)
        if not self.thinking:
            body.pop('reasoning_effort', None)
        if body["stream"]:
            body["stream_options"] = {"include_usage": True}
        messages = body["messages"]
        for message in messages:
            if isinstance(message.get("content"), list):
                for part in message["content"]:
                    if part.get("type") == "image_url":
                        part["image_url"] = dict(part["image_url"],
                            url=lossless_image_url(part["image_url"]["url"]))
        body["messages"] = messages
        return encoded

    def decode_response(self, response: bytes, decode_state: dict[str, Any]) -> DecodedResponse:
        return responses_chat.decode_response(response, decode_state)
