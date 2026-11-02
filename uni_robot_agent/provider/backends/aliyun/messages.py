"""Normalize Claude requests for Alibaba Cloud's Qwen Messages endpoint.

Contract: https://www.alibabacloud.com/help/en/model-studio/anthropic-api-messages
Only documented role/thinking conversions belong here, not context filtering.
"""
import copy

from uni_robot_agent.provider.contracts import EncodedRequest
from uni_robot_agent.provider.backends.passthrough import PassthroughAdapter


class AliyunMessagesAdapter(PassthroughAdapter):
    profile_name = "aliyun.messages"

    def __init__(self):
        super().__init__("messages")

    def encode_request(self, request):
        body = copy.deepcopy(request)
        messages = []
        for message in body.get("messages", []):
            if message.get("role") == "system":
                if not messages:
                    body["system"] = _blocks(body.get("system", [])) + _blocks(message["content"])
                    continue
                # A later notice belongs at its original turn, not in the global
                # system prompt. Role conversion is explicit for this profile.
                message["role"] = "user"
            if message.get("role") not in {"user", "assistant"}:
                raise ValueError("Qwen Messages accepts only user/assistant message roles")
            # Adjacent same-role messages form one turn. Do not discard any
            # additional message metadata while merging content blocks.
            if (messages and messages[-1]["role"] == message["role"]
                    and set(messages[-1]) == set(message) == {"role", "content"}):
                messages[-1]["content"] = _blocks(messages[-1]["content"]) + _blocks(message["content"])
            else:
                messages.append(message)
        body["messages"] = messages
        thinking = body.get("thinking")
        if isinstance(thinking, dict) and thinking.get("type") == "adaptive":
            thinking["type"] = "enabled"
        output = body.get("output_config")
        if (str(body.get("model", "")).startswith("qwen3.8") and isinstance(output, dict)
                and output.get("effort") in {"high", "max"}):
            output["effort"] = "xhigh"
        # Unlisted extensions are not evidence of rejection. Preserve them until
        # the provider contract or a focused probe establishes a required change.
        return EncodedRequest(body, {}, body)


def _blocks(content):
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return content
    raise ValueError("Messages content must be a string or a list of blocks")
