"""Codex Responses to OmniRouter's Gemini Chat Completions route."""
from uni_robot_agent.provider.conversions import responses_chat
from uni_robot_agent.provider.image_encoding import encode_chat_images


class OmniChatCompletionsAdapter:
    profile_name = "omnirouter.chat_completions"
    target_url = staticmethod(responses_chat.target_url)
    decode_response = staticmethod(responses_chat.decode_response)

    def encode_request(self, request):
        encoded = responses_chat.encode_request(request)
        encoded.body["messages"] = _tool_images_as_user_content(encoded.body["messages"])
        encode_chat_images(encoded.body["messages"])
        effort = (request.get("reasoning") or {}).get("effort")
        model = str(request.get("model", ""))
        if model.startswith("gemini-4-argon"):
            suffix = model.rsplit("-", 1)[-1]
            if suffix not in {"low", "medium", "high"}:
                raise ValueError("Use an explicit Omni Gemini route: gemini-4-argon-low/medium/high")
            if effort and effort != suffix:
                raise ValueError("Gemini route suffix must match reasoning effort")
        if effort:
            encoded.body["reasoning_effort"] = effort
        encoded.body["max_tokens"] = request.get("max_output_tokens", request.get("max_tokens", 65536))
        # Omni can return HTTP 200 with an empty Gemini SSE body. The gateway
        # already buffers replies, so request JSON and emit Responses SSE locally.
        encoded.body["stream"] = False
        return encoded


def _tool_images_as_user_content(messages: list[dict]) -> list[dict]:
    """Omni Gemini sees images in user content, not Chat tool-result content."""
    result, images = [], []
    for message in messages:
        if message["role"] != "tool" and images:
            result.append({"role": "user", "content": images})
            images = []
        content = message.get("content")
        if message["role"] != "tool" or not isinstance(content, list):
            result.append(message)
            continue
        attachments = [block for block in content if block.get("type") == "image_url"]
        if not attachments:
            result.append(message)
            continue
        identity = message["tool_call_id"]
        label = f"Images returned by tool call {identity} ({message.get('name', '')}):"
        images.extend([{"type": "text", "text": label}, *attachments])
        text = [block["text"] for block in content if block.get("type") == "text"]
        text.append(f"Image attachments for tool call {identity} follow in user content.")
        result.append(dict(message, content="\n".join(text)))
    # Keep every result in a parallel batch before the associated user images.
    if images:
        result.append({"role": "user", "content": images})
    return result
