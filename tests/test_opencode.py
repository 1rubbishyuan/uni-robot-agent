"""Responses streaming and OpenCode episode boundaries, without a CLI install."""
import json
import queue
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx

from uni_robot_agent.agents.opencode.agent import OpenCodeAgent
from uni_robot_agent.agents.opencode.transport import OpenCodeTransport
from uni_robot_agent.agents.registry import get_agent_backend
from uni_robot_agent.provider.backends.aliyun.chat_completions import AliyunChatCompletionsAdapter
from uni_robot_agent.provider.conversions.responses_chat import response_json, encode_response
from uni_robot_agent.provider.protocols.sse import decode_events


class OpenCodeTest(unittest.TestCase):
    def test_failed_server_startup_reaps_process_and_readers(self):
        with tempfile.TemporaryDirectory() as tmp:
            process = subprocess.Popen([sys.executable, '-u', '-c',
                "import time; print('http://127.0.0.1:1234', flush=True); time.sleep(30)"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
            client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503)),
                                  base_url='http://127.0.0.1:1234')
            transport = OpenCodeTransport.__new__(OpenCodeTransport)
            try:
                with patch('uni_robot_agent.agents.opencode.transport.httpx.Client', return_value=client):
                    with self.assertRaises(httpx.HTTPStatusError):
                        transport.__init__(process, Path(tmp), password='fixture', timeout=2)
                self.assertIsNotNone(process.poll())
                self.assertTrue(client.is_closed)
                self.assertTrue(all(not thread.is_alive() for thread in transport.readers))
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                process.stdout.close()
                process.stderr.close()

    def test_text_has_deltas_and_matching_message_identity(self):
        response = response_json({'id': 'r1', 'choices': [{'finish_reason': 'stop',
            'message': {'content': 'done'}}]})
        events, _ = decode_events(encode_response(response, stream=True))
        delta = next(e for e in events if e['type'] == 'response.output_text.delta')
        self.assertEqual(delta['delta'], 'done')
        self.assertEqual(delta['item_id'], response['output'][0]['id'])
        self.assertEqual(events[-1]['type'], 'response.completed')

    def test_partial_or_truncated_calls_do_not_execute(self):
        for reason, arguments in [(None, '{}'), ('length', '{}'), ('tool_calls', '{')]:
            with self.subTest(reason=reason), self.assertRaises(ValueError):
                response_json({'choices': [{'finish_reason': reason, 'message': {'tool_calls': [
                    {'id': 'a', 'function': {'name': 'move', 'arguments': arguments}}]}}]})

    def test_codex_qwen_defaults_remain_thinking(self):
        body = {'model': 'qwen', 'input': [], 'stream': True}
        self.assertTrue(AliyunChatCompletionsAdapter().encode_request(body).body['enable_thinking'])
        nonthinking = AliyunChatCompletionsAdapter(thinking=False).encode_request(body).body
        self.assertFalse(nonthinking['enable_thinking'])
        self.assertNotIn('reasoning_effort', nonthinking)

    def test_user_text_and_initial_idle_are_not_completion(self):
        agent = OpenCodeAgent(model='qwen', expected_version='reviewed')
        agent.session_id = 's1'
        agent.deadline = None
        agent.binding.exchanges = 1
        agent.transport = SimpleNamespace(events=queue.Queue())
        for event in [
            {'type': 'session.idle', 'properties': {'sessionID': 's1'}},
            {'type': 'message.part.updated', 'properties': {'part': {'sessionID': 's1',
             'messageID': 'user', 'id': 'u', 'type': 'text', 'text': 'user', 'time': {'end': 1}}}},
            {'type': 'message.updated', 'properties': {'info': {'sessionID': 's1',
             'id': 'a', 'role': 'assistant', 'time': {'completed': 1}}}},
            {'type': 'message.part.updated', 'properties': {'part': {'sessionID': 's1',
             'messageID': 'a', 'id': 'p', 'type': 'text', 'text': 'answer', 'time': {'end': 1}}}},
            {'type': 'session.idle', 'properties': {'sessionID': 's1'}},
        ]:
            agent.transport.events.put(event)
        agent.consume()
        self.assertEqual(agent.texts, ['answer'])
        self.assertFalse(agent.result().success)

    def test_registry_and_config_are_available_without_binary(self):
        self.assertIs(get_agent_backend('opencode'), OpenCodeAgent)
        agent = OpenCodeAgent(model='qwen')
        agent.mcp_url = 'http://127.0.0.1:1234/mcp/'
        config = agent.configuration('http://127.0.0.1:1235/v1', 'system')
        self.assertEqual(config['provider']['ura']['npm'], '@ai-sdk/openai')
        self.assertFalse(config['compaction']['auto'])
        self.assertNotIn('password', json.dumps(config))
