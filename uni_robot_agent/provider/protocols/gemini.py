"""GenerateContent ingress and delivery semantics for a Qwen-backed runtime."""
import copy
import json
import re
from urllib.parse import unquote, urlsplit


def response_object(body):
    from .sse import decode_events
    decoded = decode_events(body)
    if decoded is None:
        return json.loads(body)
    if len(decoded[0]) != 1:
        raise ValueError('Expected one buffered GenerateContent response')
    return decoded[0][0]


def tool_calls(body):
    response = response_object(body)
    candidate = response['candidates'][0]
    if candidate.get('finishReason') != 'STOP':
        raise ValueError('Gemini generation did not finish normally')
    return [dict(call_id=p['functionCall']['id'], name=p['functionCall']['name'],
                 arguments=p['functionCall']['args'])
            for p in candidate['content'].get('parts', []) if 'functionCall' in p]


class GeminiProtocol:
    name = 'gemini'
    trace_client = 'gemini'

    def __init__(self, model):
        self.model = model

    def matches(self, path):
        return bool(re.fullmatch(r'/v1(?:beta)?/models/[^/]+:(?:streamGenerateContent|generateContent)',
                                 urlsplit(path).path))

    def normalize_request(self, path, body):
        if not self.matches(path):
            raise ValueError('Unsupported Gemini API operation; only GenerateContent is enabled')
        route = urlsplit(path).path
        model, method = route.split('/models/', 1)[1].rsplit(':', 1)
        if unquote(model) != self.model:
            raise ValueError('Automatic model routing is disabled; use the configured Qwen model')
        if not isinstance(body, dict) or not isinstance(body.get('contents'), list):
            raise ValueError('Expected Gemini contents')
        return {**body, 'model': self.model, '_ura_stream': method == 'streamGenerateContent'}

    def generation_request(self, request, parallel_tool_calls):
        return {**copy.deepcopy(request), '_ura_parallel': parallel_tool_calls}

    def validate_context(self, context_manager):
        if context_manager.policy_metadata['name'] != 'identity':
            raise ValueError('Gemini currently supports identity context only')

    def buffered_stream_response(self, body, request, forward):
        return None

    def response_headers(self, request, headers):
        return {'Content-Type': 'text/event-stream' if request['_ura_stream'] else 'application/json'}

    def error_response(self, failure):
        return {'error': {'code': 502, 'status': 'UNAVAILABLE', 'message': failure['message']}}

    def exception_response(self, error):
        return self.error_response({'message': str(error)})

    def response_usage(self, body):
        # ModelClient archives the upstream training view (Chat), not the
        # converted Gemini reply. Read both forms without losing Qwen usage.
        from .chat_completions import parse_response
        try:
            obj = response_object(body)
            if 'usageMetadata' in obj:
                u = obj['usageMetadata']
                return {'input_tokens': u.get('promptTokenCount', 0),
                        'output_tokens': u.get('candidatesTokenCount', 0),
                        'total_tokens': u.get('totalTokenCount', 0)}
        except (ValueError, KeyError):
            pass
        return parse_response(body).get('usage', {})
