"""Observable delivery, cancellation and execution identity invariants."""
import json
import threading
import unittest

from uni_robot_agent.agents.tool_binding import DeliveredToolBinding, responses_calls
from uni_robot_agent.execution import ExecutionResult
from uni_robot_agent.tools.toolkit import ToolOutput


class Executor:
    def __init__(self):
        self.calls = []

    def call(self, name, arguments):
        self.calls.append((name, arguments))
        return ExecutionResult.completed(ToolOutput(extra={'submitted': name == 'finish'}))


class DeliveredToolsTest(unittest.TestCase):
    def setUp(self):
        self.binding = DeliveredToolBinding(responses_calls, timeout_s=0.05)
        self.binding.configure_tools([
            {'name': name, 'input_schema': {'type': 'object', 'properties': {}}}
            for name in ('move', 'finish')], ('ura_',))
        self.executor = Executor()

    def prepare(self, ids=('a',), name='move'):
        body = json.dumps({'status': 'completed', 'output': [
            {'type': 'function_call', 'name': 'ura_' + name, 'call_id': key, 'arguments': '{}'}
            for key in ids]}).encode()
        return self.binding.prepare_response(body, 'exchange_1', {})

    def invoke(self, key, name='move'):
        return self.binding.dispatch(self.executor, {'name': name,
            'arguments': {}, '_meta': {'uni_robot_agent/call_id': key}})

    def test_undelivered_response_cannot_execute(self):
        self.prepare()
        self.assertTrue(self.invoke('a')['isError'])
        self.assertEqual(self.executor.calls, [])

    def test_delivery_and_duplicate_callback(self):
        self.binding.response_written(self.prepare())
        self.assertFalse(self.invoke('a')['isError'])
        self.assertTrue(self.invoke('a')['isError'])
        self.assertEqual(len(self.executor.calls), 1)

    def test_identical_arguments_with_distinct_ids_execute_separately(self):
        self.binding.response_written(self.prepare(('a', 'b')))
        self.assertFalse(self.invoke('a')['isError'])
        self.assertFalse(self.invoke('b')['isError'])
        self.assertEqual(len(self.executor.calls), 2)

    def test_later_call_waits_for_prior_acknowledgement(self):
        self.binding.response_written(self.prepare(('a', 'b')))
        self.assertTrue(self.invoke('b')['isError'])
        self.assertEqual(self.executor.calls, [])
        self.assertFalse(self.invoke('a')['isError'])
        self.assertFalse(self.invoke('b')['isError'])

    def test_revoked_response_cannot_execute(self):
        reply = self.prepare()
        self.binding.response_failed(reply)
        self.assertTrue(self.invoke('a')['isError'])
        self.assertEqual(self.executor.calls, [])

    def test_cancellation_does_not_acknowledge_an_active_action(self):
        self.binding.response_written(self.prepare())
        entered, release = threading.Event(), threading.Event()
        original = self.executor.call

        def blocked(name, arguments):
            entered.set()
            release.wait(2)
            return original(name, arguments)

        self.executor.call = blocked
        worker = threading.Thread(target=lambda: self.invoke('a'))
        worker.start()
        self.assertTrue(entered.wait(1))
        try:
            self.binding.cancel()
            with self.assertRaises(RuntimeError):
                self.binding.drain(timeout=0.01)
            self.assertFalse(self.binding.calls['a'].acknowledged)
        finally:
            release.set()
            worker.join(2)
        self.binding.drain()

    def test_successful_finish_requires_receipt(self):
        self.binding.response_written(self.prepare(name='finish'))
        self.assertFalse(self.binding.finish_submitted)
        self.invoke('a', name='finish')
        self.assertTrue(self.binding.finish_submitted)

    def test_reused_ids_rejected_before_delivery(self):
        with self.assertRaises(ValueError):
            self.prepare(('a', 'a'))

    def test_invalid_batch_does_not_partially_register(self):
        self.binding.read_calls = lambda body: [
            {'call_id': 'a', 'name': 'ura_move', 'arguments': {}},
            {'call_id': 'b', 'name': 'ura_move', 'arguments': []}]
        with self.assertRaises(ValueError):
            self.binding.prepare_response(b'', 'exchange', {})
        self.assertEqual(self.binding.calls, {})

    def test_invalid_callback_types_are_tool_errors(self):
        for field in ('arguments', '_meta'):
            result = self.binding.dispatch(self.executor, {'name': 'move', field: 'invalid'})
            self.assertTrue(result['isError'])
        self.assertEqual(self.executor.calls, [])

    def test_queued_finish_requires_successful_job_receipt(self):
        self.binding.response_written(self.prepare(name='finish'))
        self.executor.call = lambda *args: ExecutionResult.accepted('job', 'fixture', 'queued')
        self.executor.job_result = lambda job_id, timeout: ExecutionResult.completed(
            ToolOutput(success=False, error='fixture failure'))
        self.assertTrue(self.invoke('a', name='finish')['isError'])
        self.assertFalse(self.binding.finish_submitted)

    def test_unknown_identity_and_argument_changes_are_rejected(self):
        self.binding.response_written(self.prepare())
        self.assertTrue(self.invoke('unknown')['isError'])
        result = self.binding.dispatch(self.executor, {'name': 'move',
            'arguments': {'different': True, '_uni_robot_agent_call': 'a'}})
        self.assertTrue(result['isError'])
        self.assertEqual(self.executor.calls, [])


if __name__ == '__main__':
    unittest.main()
