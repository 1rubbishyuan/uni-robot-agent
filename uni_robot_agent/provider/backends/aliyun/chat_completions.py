"""Qwen Chat settings and lossless image encoding on the shared protocol codec."""

from __future__ import annotations

from typing import Any

from uni_robot_agent.provider.conversions import responses_chat
from uni_robot_agent.provider.contracts import DecodedResponse, EncodedRequest
from uni_robot_agent.provider.image_encoding import encode_chat_images


class AliyunChatCompletionsAdapter:
    profile_name = "aliyun.chat_completions"

    def __init__(self, *, thinking=True, require_response_metadata=False):
        self.thinking = thinking
        self.require_response_metadata = require_response_metadata

    def target_url(self, upstream_base_url: str, request_path: str) -> str:
        return responses_chat.target_url(upstream_base_url, request_path)

    def encode_request(self, request: dict[str, Any]) -> EncodedRequest:
        encoded = responses_chat.encode_request(request)
        body = encoded.body
        thinking = self.thinking and (request.get("reasoning") or {}).get("effort") != "none"
        body.update(enable_thinking=thinking,
                    reasoning_effort=(request.get("reasoning") or {}).get("effort", "max"),
                    max_tokens=65536 if thinking else 8192)
        if not thinking:
            body.pop('reasoning_effort', None)
        if body["stream"]:
            body["stream_options"] = {"include_usage": True}
        encode_chat_images(body["messages"])
        return encoded

    def decode_response(self, response: bytes, decode_state: dict[str, Any]) -> DecodedResponse:
        if self.require_response_metadata:
            from uni_robot_agent.provider.protocols.chat_completions import parse_response
            body = parse_response(response)
            usage = body.get('usage') or {}
            if (not isinstance(body.get('model'), str)
                    or type(body.get('created')) not in (int, float)
                    or any(type(usage.get(key)) is not int for key in
                           ('prompt_tokens', 'completion_tokens', 'total_tokens'))):
                raise ValueError('OpenCode requires upstream model, creation time and token usage')
        return responses_chat.decode_response(response, decode_state)
