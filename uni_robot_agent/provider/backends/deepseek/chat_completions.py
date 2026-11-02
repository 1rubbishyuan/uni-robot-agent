"""DeepSeek Chat settings and session-owned Files API image transport."""

from __future__ import annotations

from typing import Any

from uni_robot_agent.provider.conversions import responses_chat
from uni_robot_agent.provider.contracts import DecodedResponse, EncodedRequest
from uni_robot_agent.provider.backends.deepseek.files import DeepSeekFiles
from uni_robot_agent.services.http import HttpTransport


class DeepSeekChatCompletionsAdapter:
    profile_name = "deepseek.chat_completions"

    def target_url(self, upstream_base_url: str, request_path: str) -> str:
        return responses_chat.target_url(upstream_base_url, request_path)

    def encode_request(self, request: dict[str, Any]) -> EncodedRequest:
        encoded = responses_chat.encode_request(request)
        body = encoded.body
        effort = (request.get("reasoning") or {}).get("effort", "high")
        body["thinking"] = {"type": "disabled" if effort == "none" else "enabled"}
        if effort != "none":
            body["reasoning_effort"] = effort
        body.setdefault("max_tokens", 65536)
        if body["stream"]:
            body["stream_options"] = {"include_usage": True}
        return encoded

    def decode_response(self, response: bytes, decode_state: dict[str, Any]) -> DecodedResponse:
        return responses_chat.decode_response(response, decode_state)

    def open_session(self, transport: HttpTransport, base_url: str) -> DeepSeekFiles:
        return DeepSeekFiles(transport, base_url)
