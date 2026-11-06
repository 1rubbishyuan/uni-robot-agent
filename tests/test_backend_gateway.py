"""Exercise gateway delivery and authenticated MCP in-process, without sockets."""
import json
import gzip
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx

from uni_robot_agent.agents.tool_binding import DeliveredToolBinding, responses_calls
from uni_robot_agent.agents.gemini_cli.agent import GeminiCliAgent
from uni_robot_agent.artifacts.store import ArtifactStore
from uni_robot_agent.events import EventBus, ModelRequest
from uni_robot_agent.provider.backends.aliyun.chat_completions import AliyunChatCompletionsAdapter
from uni_robot_agent.provider.backends.aliyun.gemini_chat_completions import AliyunGeminiChatAdapter
from uni_robot_agent.provider.gateway import ProviderGateway
from uni_robot_agent.provider.protocols.gemini import GeminiProtocol, tool_calls
from uni_robot_agent.services.hosting import create_app
from uni_robot_agent.services.mcp.http import HttpMcpServer
from test_delivered_tools import Executor


async def connected():
    return False


async def receive():
    return {'type': 'http.request', 'body': b'', 'more_body': False}


class BackendGatewayTest(unittest.IsolatedAsyncioTestCase):
    def gateway(self, gemini=False):
        binding = DeliveredToolBinding(tool_calls if gemini else responses_calls)
        binding.configure_tools([{'name': 'move', 'input_schema': {'type': 'object'}}], ('ura_',))
        bus = EventBus()
        events = []
        bus.subscribe(events.append)
        gateway = ProviderGateway('https://provider.invalid/v1', model_binding=binding, event_bus=bus,
            adapter=AliyunGeminiChatAdapter() if gemini else AliyunChatCompletionsAdapter(thinking=False),
            protocol=GeminiProtocol('qwen') if gemini else None)
        raw = json.dumps({'id': 'r1', 'choices': [{'finish_reason': 'tool_calls', 'message': {
            'tool_calls': [{'id': 'a', 'function': {'name': 'ura_move', 'arguments': '{}'}}]}}]}).encode()
        gateway._client.send = lambda **kwargs: (200, {'Content-Type': 'application/json'}, raw)
        self.addCleanup(gateway.stop)
        return gateway, binding, events

    def forward(self, gateway, gemini=False):
        body = {'contents': [{'role': 'user', 'parts': [{'text': 'move'}]}]} if gemini else {
            'model': 'qwen', 'input': [{'role': 'user', 'content': 'move'}], 'stream': True}
        path = '/v1beta/models/qwen:streamGenerateContent?alt=sse' if gemini else '/v1/responses'
        return gateway._forward(path, json.dumps(body).encode(), {},
                                SimpleNamespace(is_disconnected=connected))

    async def test_delivery_commits_identity_for_both_protocols(self):
        for gemini in (False, True):
            with self.subTest(gemini=gemini):
                gateway, binding, _ = self.gateway(gemini)
                response = self.forward(gateway, gemini)
                self.assertEqual(response.status_code, 200, response.body)
                self.assertTrue(all(not call.delivered for call in binding.calls.values()))
                messages = []

                async def send(message):
                    messages.append(message)

                await response({'type': 'http', 'method': 'POST'}, receive, send)
                self.assertEqual(messages[-1]['type'], 'http.response.body')
                self.assertEqual(binding.exchanges, 1)
                self.assertTrue(gateway.trace_records[0]['complete'])
                key = next(iter(binding.calls))
                executor = Executor()
                reply = binding.dispatch(executor, {'name': 'move', 'arguments': {},
                    '_meta': {'uni_robot_agent/call_id': key}})
                self.assertFalse(reply['isError'])
                self.assertEqual(len(executor.calls), 1)

    async def test_failed_asgi_delivery_revokes_proposed_motion(self):
        gateway, binding, _ = self.gateway()
        response = self.forward(gateway)

        async def send(message):
            if message['type'] == 'http.response.body':
                raise ConnectionResetError('fixture disconnect')

        await response({'type': 'http', 'method': 'POST'}, receive, send)
        self.assertTrue(binding.calls['a'].revoked)
        self.assertFalse(gateway.trace_records[0]['complete'])
        self.assertTrue(gateway.trace_records[0]['client_disconnected'])
        with self.assertRaises(RuntimeError):
            binding.claim('a', 'move', {})

    async def test_budget_rejection_is_recorded_without_an_upstream_call(self):
        gateway, binding, events = self.gateway()
        binding.max_exchanges = binding.exchanges = 1
        gateway._client.send = lambda **kwargs: self.fail('Budget rejection reached upstream')
        response = self.forward(gateway)
        self.assertEqual(response.status_code, 502)

        async def send(message):
            pass

        await response({'type': 'http', 'method': 'POST'}, receive, send)
        self.assertTrue(binding.limit_reached)
        self.assertIn('limit reached', gateway.trace_records[0]['error'])
        self.assertTrue(any(isinstance(e, ModelRequest) and e.success is False for e in events))

    async def test_mcp_auth_and_explicit_identity_are_independent(self):
        calls = []
        server = HttpMcpServer(Executor(), auth_token='fixture-token',
            call_handler=lambda params: calls.append(params) or {'content': [], 'isError': False})
        transport = httpx.ASGITransport(app=create_app(server._router()))
        body = {'jsonrpc': '2.0', 'id': 'request', 'method': 'tools/call',
                'params': {'name': 'move', 'arguments': {}, '_meta': {'uni_robot_agent/call_id': 'model'}}}
        async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
            denied = await client.post('/mcp/', json=body)
            self.assertEqual(denied.status_code, 401)
            self.assertEqual(calls, [])
            reply = await client.post('/mcp/', json=body, headers={'Authorization': 'Bearer fixture-token'})
            self.assertEqual(reply.json()['id'], 'request')
            self.assertEqual(calls[0]['_meta']['uni_robot_agent/call_id'], 'model')

    async def test_rejected_gemini_routes_cancel_owner_and_archive_failure(self):
        cases = [
            ('/v1beta/models/other:generateContent', {'contents': []}, 'routing is disabled'),
            ('/v1beta/models/qwen:countTokens', {'contents': []}, 'Unsupported Gemini API'),
            ('/v1beta/models/qwen:generateContent', {'contents': 'invalid'}, 'Expected Gemini contents'),
        ]
        for path, body, message in cases:
            with self.subTest(path=path), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                artifacts = ArtifactStore(root)
                agent = GeminiCliAgent(model='qwen')
                bus, events = EventBus(), []
                bus.subscribe(events.append)
                bus.subscribe(agent.on_model_event)
                gateway = ProviderGateway('https://provider.invalid/v1',
                    trace_dir=root / 'trace', artifacts=artifacts,
                    adapter=AliyunGeminiChatAdapter(), protocol=GeminiProtocol('qwen'),
                    model_binding=agent.binding, event_bus=bus)
                gateway._client.send = lambda **kwargs: self.fail('Rejected route reached upstream')
                try:
                    with patch('uni_robot_agent.provider.gateway.HttpServer') as host:
                        gateway.start()
                        app = host.call_args.args[0]
                        transport = httpx.ASGITransport(app=app)
                        async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
                            response = await client.post(path, json=body)
                    self.assertEqual(response.status_code, 502)
                    self.assertIn(message, response.json()['error']['message'])
                    self.assertTrue(agent.stopped.is_set())
                    self.assertEqual(agent.stop_reason, 'provider_error')
                    self.assertFalse(agent.binding.calls)
                    self.assertEqual(agent.binding.exchanges, 0)
                    finished = [e for e in events if isinstance(e, ModelRequest) and e.phase == 'finished']
                    self.assertEqual(len(finished), 1)
                    self.assertIs(finished[0].success, False)
                    record = gateway.trace_records[0]
                    self.assertFalse(record['complete'])
                    self.assertIn(message, record['error'])
                    self.assertEqual(record['provider_attempts'], [])
                    artifacts.close()
                    archive = root / 'trace/exchange_0000/request.gemini.json.gz'
                    self.assertEqual(json.loads(gzip.decompress(archive.read_bytes())), body)
                finally:
                    gateway.stop()
                    gateway._client.close()
                    artifacts.close()
