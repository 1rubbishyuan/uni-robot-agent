"""Preserve Tencent Responses semantics while encoding PNG images losslessly."""

from __future__ import annotations

import copy
from typing import Any

from uni_robot_agent.provider.backends.passthrough import responses_url
from uni_robot_agent.provider.contracts import DecodedResponse, EncodedRequest
from uni_robot_agent.provider.image_encoding import lossless_image_url


class TencentResponsesAdapter:
    profile_name = "tencent.responses"

    def target_url(self, upstream_base_url: str, request_path: str) -> str:
        return responses_url(upstream_base_url, request_path)

    def encode_request(self, request: dict[str, Any]) -> EncodedRequest:
        result = copy.deepcopy(request)
        for item in result.get("input", []):
            if not isinstance(item, dict):
                continue
            for field in ("content", "output"):
                parts = item.get(field)
                if not isinstance(parts, list):
                    continue
                for part in parts:
                    if isinstance(part, dict) and part.get("type") == "input_image" and "image_url" in part:
                        part["image_url"] = lossless_image_url(part["image_url"])
        return EncodedRequest(result, {}, copy.deepcopy(result))

    def decode_response(self, response: bytes, decode_state: dict[str, Any]) -> DecodedResponse:
        return DecodedResponse(response, response)
