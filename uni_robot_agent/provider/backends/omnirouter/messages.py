"""Codex Responses to OmniRouter's native Claude Messages endpoint."""
from uni_robot_agent.provider.conversions import responses_messages
from uni_robot_agent.provider.image_encoding import encode_messages_images


class OmniMessagesAdapter:
    profile_name = "omnirouter.messages"

    def __init__(self):
        self._reasoning_history = {}

    def target_url(self, upstream_base_url, request_path):
        return upstream_base_url.rstrip("/").removesuffix("/v1") + "/v1/messages"

    def encode_request(self, request):
        encoded = responses_messages.encode_request(request, self._reasoning_history)
        encode_messages_images(encoded.body["messages"])
        effort = (request.get("reasoning") or {}).get("effort", "medium")
        if effort not in {"low", "medium", "high", "max"}:
            raise ValueError("Claude adaptive thinking requires low, medium, high or max effort")
        encoded.body["thinking"] = {"type": "adaptive"}
        encoded.body["output_config"] = {"effort": effort}
        return encoded

    def decode_response(self, response, decode_state):
        return responses_messages.decode_response(response, decode_state, self._reasoning_history)

    def open_session(self, transport, base_url):
        return _MessagesSession(self._reasoning_history)


class _MessagesSession:
    def __init__(self, history):
        self.history = history

    def prepare_request(self, body, headers, exchange):
        headers["anthropic-version"] = "2023-06-01"

    def finish_request(self, exchange):
        pass

    def close(self):
        self.history.clear()
