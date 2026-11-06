import json
import unittest

from uni_robot_agent.provider.conversions.gemini_chat import encode_request, decode_response
from uni_robot_agent.provider.protocols.gemini import GeminiProtocol, response_object, tool_calls
from uni_robot_agent.provider.backends.aliyun.gemini_chat_completions import AliyunGeminiChatAdapter


class GeminiConversionTest(unittest.TestCase):
    def request(self, contents):
        return {'model': 'qwen-test', 'contents': contents}

    def test_text_image_and_distinct_identical_calls_round_trip(self):
        encoded = encode_request(self.request([
            {'role': 'model', 'parts': [
                {'functionCall': {'id': key, 'name': 'view', 'args': {}}} for key in ['a', 'b']]},
            {'role': 'user', 'parts': [
                {'functionResponse': {'id': 'a', 'name': 'view', 'response': {'ok': True}}},
                {'functionResponse': {'id': 'b', 'name': 'view', 'response': {'ok': True}}},
                {'inlineData': {'mimeType': 'image/png', 'data': 'aGVsbG8='}},
            ]},
        ]))
        messages = encoded.body['messages']
        self.assertEqual([m['role'] for m in messages], ['assistant', 'tool', 'tool', 'user'])
        self.assertEqual([m['tool_call_id'] for m in messages[1:3]], ['a', 'b'])
        self.assertEqual(messages[3]['content'][0]['image_url']['url'], 'data:image/png;base64,aGVsbG8=')

    def test_response_without_identity_is_not_matched_by_name(self):
        with self.assertRaises(ValueError):
            encode_request(self.request([{'parts': [{'functionResponse': {'name': 'view', 'response': {}}}]}]))

    def test_duplicate_and_unresolved_history_is_rejected(self):
        with self.assertRaises(ValueError):
            encode_request(self.request([{'role': 'model', 'parts': [
                {'functionCall': {'id': 'a', 'name': 'view'}}]}]))

    def response(self, **changes):
        payload = {'id': 'qwen-response', 'choices': [{'finish_reason': 'tool_calls', 'message': {
            'content': '', 'tool_calls': [{'id': '1', 'function': {'name': 'mcp_ura_view', 'arguments': '{}'}}]}}],
            'usage': {'prompt_tokens': 7, 'completion_tokens': 9, 'total_tokens': 16}}
        payload.update(changes)
        return json.dumps(payload).encode()

    def test_reply_retains_usage_and_explicit_call_identity(self):
        decoded = decode_response(self.response(), {'stream': True})
        self.assertEqual(tool_calls(decoded.body)[0]['call_id'], 'mcp_ura_view__1')
        self.assertEqual(response_object(decoded.body)['usageMetadata']['totalTokenCount'], 16)
        self.assertEqual(decoded.training_body, self.response())

    def test_incomplete_response_cannot_propose_a_tool(self):
        with self.assertRaises(ValueError):
            decode_response(self.response(choices=[{'message': {'content': 'partial'}}]), {'stream': True})

    def test_model_path_is_normalized_and_automatic_routing_rejected(self):
        protocol = GeminiProtocol('qwen-test')
        normalized = protocol.normalize_request('/v1beta/models/qwen-test:streamGenerateContent?alt=sse', {'contents': []})
        self.assertEqual(normalized['model'], 'qwen-test')
        self.assertTrue(normalized['_ura_stream'])
        with self.assertRaises(ValueError):
            protocol.normalize_request('/v1beta/models/other:generateContent', {'contents': []})
        with self.assertRaises(ValueError):
            protocol.normalize_request('/v1beta/models/qwen-test:countTokens', {'contents': []})

    def test_gemini_credentials_are_not_forwarded(self):
        adapter = AliyunGeminiChatAdapter()
        adapter.api_key = 'upstream-test-key'
        session = adapter.open_session(None, '')
        headers = {'x-goog-api-key': 'local-key', 'Authorization': 'Bearer local', 'Content-Type': 'application/json'}
        session.prepare_request({}, headers, {})
        self.assertNotIn('x-goog-api-key', headers)
        self.assertEqual(headers['Authorization'], 'Bearer upstream-test-key')

    def test_schema_and_forced_tool(self):
        request = self.request([{'parts': [{'text': 'hello'}]}])
        request['tools'] = [{'functionDeclarations': [{'name': 'view', 'parameters': {
            'type': 'OBJECT', 'properties': {'x': {'type': 'STRING', 'nullable': True}}}}]}]
        request['toolConfig'] = {'functionCallingConfig': {'mode': 'ANY', 'allowedFunctionNames': ['view']}}
        body = encode_request(request).body
        self.assertEqual(body['tools'][0]['function']['parameters']['properties']['x']['type'], ['string', 'null'])
        self.assertEqual(body['tool_choice']['function']['name'], 'view')

    def test_unknown_generation_fields_are_not_silently_dropped(self):
        request = self.request([])
        request['generationConfig'] = {'thinkingConfig': {'thinkingBudget': 100}}
        with self.assertRaises(ValueError):
            encode_request(request)


if __name__ == '__main__':
    unittest.main()
