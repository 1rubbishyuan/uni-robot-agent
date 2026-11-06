"""Convert Gemini requests to Qwen Chat without changing the agent loop."""
from uni_robot_agent.provider.conversions import gemini_chat
from uni_robot_agent.provider.contracts import EncodedRequest


class AliyunGeminiChatAdapter:
    profile_name = 'aliyun.gemini_chat_completions'

    def __init__(self):
        self.api_key = None

    def target_url(self, upstream_base_url, request_path):
        return upstream_base_url.rstrip('/') + '/chat/completions'

    def encode_request(self, request):
        import copy
        encoded = gemini_chat.encode_request(request)
        # Non-thinking mode is the initial compatibility baseline. Qwen thinking
        # history is a separate capability, not a fabricated Google signature.
        encoded.body['enable_thinking'] = False
        encoded.body.setdefault('max_tokens', 8192)
        encoded.body['stream_options'] = {'include_usage': True}
        return EncodedRequest(encoded.body, encoded.decode_state, copy.deepcopy(encoded.body))

    decode_response = staticmethod(gemini_chat.decode_response)

    def open_session(self, transport, base_url):
        return _Authentication(self.api_key)


class _Authentication:
    def __init__(self, api_key):
        self.api_key = api_key

    def prepare_request(self, body, headers, exchange):
        for key in list(headers):
            if key.lower() in {'x-goog-api-key', 'authorization', 'x-goog-api-client'}:
                del headers[key]
        if self.api_key:
            headers['Authorization'] = 'Bearer ' + self.api_key

    def finish_request(self, exchange):
        pass

    def close(self):
        pass
