"""GenerateContent ingress and delivery semantics for a Qwen-backed runtime."""
import copy
import json
import re
from urllib.parse import unquote, urlsplit

from uni_robot_agent.provider.contracts import ModelResponse


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

    def local_response(self, path):
        match = re.fullmatch(r'/v1(?:beta)?/models/([^/]+):countTokens', urlsplit(path).path)
        if match is None or unquote(match[1]) != self.model:
            return None
        # The pinned CLI catches this non-retryable error and estimates locally.
        # Do not invent Qwen token counts or treat media estimation as generation.
        body = {'error': {'code': 400, 'status': 'INVALID_ARGUMENT',
                         'message': 'countTokens is unsupported by the Qwen bridge'}}
        return ModelResponse(400, {'Content-Type': 'application/json'},
                             json.dumps(body).encode(), ())

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
        if context_manager.policy_metadata['name'] not in {'identity', 'sliding_window'}:
            raise ValueError('Gemini supports identity or sliding_window context')

    def buffered_stream_response(self, body, request, forward):
        return None

    def response_headers(self, request, headers):
        return {'Content-Type': 'text/event-stream' if request['_ura_stream'] else 'application/json'}

    def error_response(self, failure):
        return {'error': {'code': 502, 'status': 'UNAVAILABLE', 'message': failure['message']}}

    def exception_response(self, error):
        return self.error_response({'message': str(error)})

    def response_failure(self, body):
        try:
            error = response_object(body).get('error')
        except (ValueError, TypeError):
            return {'code': 'invalid_response', 'message': 'Invalid Gemini response'}
        if error:
            return {'code': str(error.get('code', 'provider_error')),
                    'message': str(error.get('message', error))}
        return None

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
        try:
            usage = parse_response(body).get('usage') or {}
            return {'input_tokens': usage.get('prompt_tokens', 0),
                    'output_tokens': usage.get('completion_tokens', 0),
                    'total_tokens': usage.get('total_tokens', 0)}
        except (ValueError, TypeError, AttributeError):
            return {}
