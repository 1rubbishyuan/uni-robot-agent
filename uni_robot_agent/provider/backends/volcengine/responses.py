"""Adapt native Responses tools and images to ARK's supported wire format."""

from __future__ import annotations

import copy
import json
from typing import Any

from uni_robot_agent.provider.contracts import DecodedResponse, EncodedRequest
from uni_robot_agent.provider.backends.passthrough import responses_url
from uni_robot_agent.provider.conversions.responses_chat import function_tool
from uni_robot_agent.provider.image_encoding import lossless_image_url
from uni_robot_agent.provider.protocols.sse import decode_events, encode_events
from uni_robot_agent.provider.conversions.tool_names import flatten_request, restore_response


class VolcengineResponsesAdapter:
    profile_name = "volcengine.responses"

    def __init__(self, *, lossless_images: bool = False):
        self._lossless_images = lossless_images

    def target_url(self, upstream_base_url: str, request_path: str) -> str:
        return responses_url(upstream_base_url, request_path)

    def encode_request(self, request: dict[str, Any]) -> EncodedRequest:
        result, routes = flatten_request(request)
        declarations = [result]
        for item in result.get("input", []):
            if not isinstance(item, dict):
                continue
            if item.get("type") == "additional_tools":
                declarations.append(item)
            if self._lossless_images:
                for field in ("content", "output"):
                    parts = item.get(field)
                    if isinstance(parts, list):
                        for part in parts:
                            if isinstance(part, dict) and part.get("type") == "input_image" and "image_url" in part:
                                part["image_url"] = lossless_image_url(part["image_url"])
        for declaration in declarations:
            if "tools" in declaration:
                declaration["tools"] = [
                    function_tool(tool) if isinstance(tool, dict) and tool.get("type") == "web_search" else tool
                    for tool in declaration["tools"]
                ]
        _convert_search_choice(result.get("tool_choice"))
        return EncodedRequest(result, {"routes": routes}, copy.deepcopy(result))

    def decode_response(self, response: bytes, decode_state: dict[str, Any]) -> DecodedResponse:
        routes = decode_state["routes"]
        events = decode_events(response)
        if events is None:
            restored = json.dumps(restore_response(json.loads(response), routes), ensure_ascii=False).encode()
        else:
            payloads, has_done = events
            restored = encode_events(restore_response(payloads, routes), has_done=has_done)
        return DecodedResponse(restored, response)


def _convert_search_choice(choice: Any) -> None:
    if not isinstance(choice, dict):
        return
    if choice.get("type") == "web_search":
        choice.clear()
        choice.update(type="function", name="web_search")
    for tool in choice.get("tools", []):
        _convert_search_choice(tool)
