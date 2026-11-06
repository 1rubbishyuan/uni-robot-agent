"""ACP pipe behavior and explicit source compatibility boundaries."""
import hashlib
import io
import json
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from uni_robot_agent.agents.gemini_cli.agent import BRIDGE_ID, GeminiCliAgent
from uni_robot_agent.agents.gemini_cli.bridge import HELPER, SOURCE, patch_source, seal
from uni_robot_agent.agents.gemini_cli.manifest import bundle_inventory
from uni_robot_agent.agents.gemini_cli.transport import AcpTransport
from uni_robot_agent.agents.managed import stop_process


class GeminiCliTest(unittest.TestCase):
    def test_subprocess_notifications_and_response_use_stdio(self):
        code = '''import json,sys
for line in sys.stdin:
    request=json.loads(line)
    print(json.dumps({'jsonrpc':'2.0','method':'session/update','params':{
        'sessionId':'s1','update':{'sessionUpdate':'agent_message_chunk',
        'content':{'type':'text','text':'hello'}}}}), flush=True)
    print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':{'stopReason':'end_turn'}}), flush=True)
'''
        with tempfile.TemporaryDirectory() as tmp:
            process = subprocess.Popen([sys.executable, '-u', '-c', code], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
            transport = AcpTransport(process, Path(tmp))
            notifications = []
            try:
                result = transport.request('session/prompt', {}, check_running=lambda: None,
                    on_notification=lambda *args: notifications.append(args), timeout=2)
                self.assertEqual(result['stopReason'], 'end_turn')
                self.assertEqual(notifications[0][1]['sessionId'], 's1')
            finally:
                stop_process(process)
                transport.close()
                for stream in (process.stdin, process.stdout, process.stderr):
                    stream.close()

    def request_from_messages(self, *messages):
        transport = AcpTransport.__new__(AcpTransport)
        transport.sequence = 0
        transport.events = queue.Queue()
        sent = []
        transport.send = sent.append
        for message in messages:
            transport.events.put(message)
        return transport, sent

    def test_wrong_response_id_and_eof_fail_without_retry(self):
        for message in [{'id': 99, 'result': {}}, EOFError('closed'), {'id': 1, 'error': {'code': -1}}]:
            transport, sent = self.request_from_messages(message)
            with self.assertRaises((RuntimeError, EOFError)):
                transport.request('session/new', {}, check_running=lambda: None,
                    on_notification=lambda *args: None, timeout=0.1)
            self.assertEqual(len(sent), 1)

    def test_interactive_permission_is_cancelled(self):
        transport, sent = self.request_from_messages({'id': 'permission',
            'method': 'session/request_permission', 'params': {}})
        with self.assertRaises(RuntimeError):
            transport.request('session/prompt', {}, check_running=lambda: None,
                on_notification=lambda *args: None)
        self.assertEqual(sent[-1]['result']['outcome']['outcome'], 'cancelled')

    def test_bridge_requires_one_reviewed_seam(self):
        source = 'DiscoveredMCPTool; const toolResult: ToolResult = await invocation.execute({ abortSignal, });'
        patched = patch_source(source)
        self.assertIn('callRobotTool(tool, args, callId, abortSignal)', patched)
        self.assertIn("tool.serverName === 'ura'", patched)
        for invalid in ['', source + source, patched]:
            with self.assertRaises(ValueError):
                patch_source(invalid)

    def test_unpatched_or_changed_bundle_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / 'bundle.js'
            binary.write_text('// ' + BRIDGE_ID)
            manifest = Path(tmp) / 'manifest.json'
            manifest.write_text(json.dumps({'bridge': BRIDGE_ID, 'version': 'candidate',
                'sha256': hashlib.sha256(binary.read_bytes()).hexdigest()}))
            agent = GeminiCliAgent(model='qwen', binary=str(binary), expected_version='candidate',
                                   bridge_manifest=str(manifest))
            agent.verify_runtime()
            binary.write_text('unpatched')
            with self.assertRaises(ValueError):
                agent.verify_runtime()

    def test_notification_session_identity(self):
        agent = GeminiCliAgent(model='qwen')
        agent.session_id = 's1'
        with self.assertRaises(RuntimeError):
            agent.on_notification('session/update', {'sessionId': 's2'})
        agent.on_notification('session/update', {'sessionId': 's1', 'update': {
            'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': 'answer'}}})
        self.assertEqual(agent.texts, ['answer'])
        self.assertFalse(agent.result().success)

    def test_seal_and_verify_split_bundle_with_policy_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkout = Path(tmp)
            (checkout / SOURCE).parent.mkdir(parents=True)
            source = {'bridge': BRIDGE_ID, 'revision': 'a' * 40}
            for path, key in [(SOURCE, 'patched_sha256'), (HELPER, 'helper_sha256')]:
                (checkout / path).write_text('reviewed source')
                source[key] = hashlib.sha256(b'reviewed source').hexdigest()
            (checkout / 'ura-bridge-source.json').write_text(json.dumps(source))
            bundle = checkout / 'bundle'
            bundle.mkdir()
            binary = bundle / 'gemini.js'
            binary.write_text('#!/bin/sh\necho candidate\n')
            binary.chmod(0o700)
            (bundle / 'chunk.mjs').write_text('// ' + BRIDGE_ID)
            (bundle / 'policy.toml').write_text('rule = "trusted"\n')
            manifest = checkout / 'manifest.json'
            with patch('uni_robot_agent.agents.gemini_cli.bridge.git', return_value='a' * 40):
                seal(checkout, binary, 'candidate', manifest)
                with self.assertRaisesRegex(ValueError, 'outside'):
                    seal(checkout, binary, 'candidate', bundle / 'manifest.json')
            agent = GeminiCliAgent(model='qwen', binary=str(binary), expected_version='candidate',
                                   bridge_manifest=str(manifest))
            agent.verify_runtime()
            original = {p: p.read_bytes() for p in bundle.iterdir()}
            for name, content in [('chunk.mjs', '// ' + BRIDGE_ID + ' modified'),
                                  ('policy.toml', 'rule = "changed"'),
                                  ('extra.js', '// unexpected file')]:
                with self.subTest(name=name):
                    path = bundle / name
                    path.write_text(content)
                    with self.assertRaises(ValueError):
                        agent.verify_runtime()
                    if path in original:
                        path.write_bytes(original[path])
                    else:
                        path.unlink()
            (bundle / 'policy.toml').unlink()
            with self.assertRaises(ValueError):
                agent.verify_runtime()
            (bundle / 'policy.toml').write_bytes(original[bundle / 'policy.toml'])
            binary.write_text('changed entry point')
            with self.assertRaises(ValueError):
                agent.verify_runtime()

    def test_bundle_rejects_symlinks_missing_bridge_and_unknown_formats(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / 'bundle'
            bundle.mkdir()
            binary = bundle / 'gemini.js'
            binary.write_text('// ' + BRIDGE_ID)
            manifest = Path(tmp) / 'manifest.json'
            record = {'bridge': BRIDGE_ID, 'version': 'candidate', 'format': 2,
                      'entrypoint': binary.name, 'files': bundle_inventory(bundle)}
            manifest.write_text(json.dumps(record))
            agent = GeminiCliAgent(model='qwen', binary=str(binary), expected_version='candidate',
                                   bridge_manifest=str(manifest))
            (bundle / 'linked.js').symlink_to(binary)
            with self.assertRaisesRegex(ValueError, 'symbolic links'):
                agent.verify_runtime()
            (bundle / 'linked.js').unlink()
            binary.write_text('// unpatched')
            with self.assertRaisesRegex(ValueError, 'compatibility bridge'):
                bundle_inventory(bundle)
            binary.write_text('// ' + BRIDGE_ID)
            for change in [{'format': 3}, {'entrypoint': 'other.js'}]:
                manifest.write_text(json.dumps({**record, **change}))
                with self.assertRaises(ValueError):
                    agent.verify_runtime()
