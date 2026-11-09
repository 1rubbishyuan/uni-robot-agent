"""Pinned Antigravity 0.1.23 GenerateContent dialect for Qwen tool calling.

Keep these native-runtime quirks out of the Gemini CLI protocol. The original
request remains archived by the gateway before normalization.
"""
import copy

from .gemini import GeminiProtocol


class AntigravityProtocol(GeminiProtocol):
    def normalize_request(self, path, body):
        request = copy.deepcopy(super().normalize_request(path, body))
        session = request.pop('sessionId', None)
        if session is not None and not isinstance(session, str):
            raise ValueError('Antigravity sessionId must be a string')
        for content in request['contents']:
            parts = content.get('parts', [])
            if content.get('role') == 'model' and any('functionResponse' in p for p in parts):
                if not all('functionResponse' in p for p in parts):
                    raise ValueError('Antigravity mixed model/result content is unsupported')
                content['role'] = 'user'
        # The pinned native harness emits NONE even with explicitly enabled,
        # eagerly declared MCP tools. This backend explicitly runs tool_calling;
        # restore automatic selection over those declarations for Qwen only.
        if request.get('tools') and request.get('toolConfig') == {
                'functionCallingConfig': {'mode': 'NONE'}}:
            request['toolConfig'] = {'functionCallingConfig': {'mode': 'AUTO'}}
        return request
