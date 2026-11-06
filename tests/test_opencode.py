"""Responses streaming and OpenCode episode boundaries, without a CLI install."""
import json
import fnmatch
import os
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
    def test_command_requests_use_bounded_runtime_budget(self):
        observed = []
        def respond(request):
            observed.append(request.extensions['timeout']['read'])
            return httpx.Response(200, json={'id': 'session'})
        transport = OpenCodeTransport.__new__(OpenCodeTransport)
        transport.command_timeout = 120
        with httpx.Client(transport=httpx.MockTransport(respond),
                          base_url='http://127.0.0.1') as client:
            transport.client = client
            self.assertEqual(transport.request('POST', '/session'), {'id': 'session'})
            transport.request('GET', '/global/health', timeout=0.5)
        self.assertEqual(observed, [120, 0.5])

    def test_sse_startup_error_is_reported_and_process_reaped(self):
        with tempfile.TemporaryDirectory() as tmp:
            process = subprocess.Popen([sys.executable, '-u', '-c',
                "import time; print('http://127.0.0.1:1234', flush=True); time.sleep(30)"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
            client = httpx.Client(transport=httpx.MockTransport(lambda request:
                httpx.Response(403 if request.url.path == '/event' else 200, json={})),
                base_url='http://127.0.0.1:1234')
            transport = OpenCodeTransport.__new__(OpenCodeTransport)
            try:
                with patch('uni_robot_agent.agents.opencode.transport.httpx.Client', return_value=client):
                    with self.assertRaises(httpx.HTTPStatusError) as error:
                        transport.__init__(process, Path(tmp), password='fixture', timeout=2)
                self.assertEqual(error.exception.response.status_code, 403)
                self.assertIsNotNone(process.poll())
                self.assertFalse(transport.consumer.is_alive())
                self.assertTrue(client.is_closed)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                process.stdout.close()
                process.stderr.close()

    def test_stream_preserves_sdk_required_metadata_and_usage(self):
        adapter = AliyunChatCompletionsAdapter(thinking=False, require_response_metadata=True)
        encoded = adapter.encode_request({'model': 'qwen', 'input': [], 'stream': True})
        chunks = [
            {'id': 'r', 'created': 123, 'model': 'qwen', 'choices': [
                {'delta': {'content': 'done'}, 'finish_reason': 'stop'}]},
            {'choices': [], 'usage': {'prompt_tokens': 5, 'completion_tokens': 2, 'total_tokens': 7}},
        ]
        raw = ''.join('data: ' + json.dumps(chunk) + '\n\n' for chunk in chunks).encode()
        events, _ = decode_events(adapter.decode_response(raw, encoded.decode_state).body)
        self.assertEqual(events[0]['response']['created_at'], 123)
        self.assertEqual(events[0]['response']['model'], 'qwen')
        self.assertEqual(events[-1]['response']['usage']['total_tokens'], 7)
        with self.assertRaisesRegex(ValueError, 'token usage'):
            adapter.decode_response(('data: ' + json.dumps(chunks[0]) + '\n\n').encode(), encoded.decode_state)

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
        config = agent.configuration('http://127.0.0.1:1235/v1', 'system', session_dir='/runs/episode')
        self.assertEqual(config['provider']['ura']['npm'], '@ai-sdk/openai')
        self.assertFalse(config['compaction']['auto'])
        self.assertNotIn('password', json.dumps(config))

    def test_episode_artifacts_and_scratch_are_allowed_without_sibling_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            episode = Path(tmp).resolve() / 'episode'
            agent = OpenCodeAgent(model='qwen')
            agent.mcp_url = 'http://127.0.0.1:1234/mcp/'
            config = agent.configuration('http://127.0.0.1:1235/v1', 'system', session_dir=episode)
            rules = config['agent']['robot']['permission']['external_directory']
            def permission(path):
                return next(action for pattern, action in reversed(list(rules.items()))
                            if fnmatch.fnmatchcase(str(path), pattern))
            self.assertEqual(permission(episode / 'images' / '*'), 'allow')
            self.assertEqual(permission(episode / 'scratch' / '*'), 'allow')
            self.assertEqual(permission(episode / 'opencode' / 'tmp' / 'opencode' / '*'), 'allow')
            self.assertEqual(permission(episode.parent / 'another-episode' / 'images' / '*'), 'deny')
            self.assertEqual(permission(episode.parent / 'episode-other' / '*'), 'deny')
            self.assertEqual(permission('/tmp/opencode/*'), 'deny')

    def test_cli_scratch_directory_is_owned_by_the_episode(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = OpenCodeAgent(model='qwen')
            agent.directory = Path(tmp).resolve() / 'opencode'
            agent.directory.mkdir()
            agent.workspace = agent.directory / 'workspace'
            agent.workspace.mkdir()
            agent.env = {}
            agent.mcp_url = 'http://127.0.0.1:1234/mcp/'
            agent.mcp_token = 'fixture'
            inherited_tmp = os.environ.get('TMPDIR')
            with patch.object(agent, 'begin'), patch(
                    'uni_robot_agent.agents.opencode.agent.subprocess.Popen',
                    side_effect=RuntimeError('fixture stops before launching')) as launch:
                agent.run(prompt=SimpleNamespace(system='system', user='user'), task='task',
                          toolkit=None, executor=None, session_dir=tmp,
                          model_endpoint='http://127.0.0.1:1235/v1')
            scratch = Path(launch.call_args.kwargs['env']['TMPDIR'])
            self.assertEqual(scratch, agent.directory / 'tmp')
            self.assertTrue(scratch.is_dir())
            self.assertEqual(os.environ.get('TMPDIR'), inherited_tmp)
