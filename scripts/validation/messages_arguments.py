"""Validate empty and malformed streamed tool arguments independently."""
import json
import unittest
from uni_robot_agent.provider.protocols.messages import message_response, response_failure


def stream(fragment, *, stop=True):
    events = [
        {"type": "message_start", "message": {"id": "msg_fixture", "type": "message", "role": "assistant", "content": [], "stop_reason": None}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "call_fixture", "name": "observe", "input": {}}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": fragment}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
    ]
    if stop:
        events.append({"type": "message_stop"})
    return "".join("data: " + json.dumps(event) + "\n\n" for event in events).encode()


class MessagesArgumentsTests(unittest.TestCase):
    def test_empty_delta_preserves_initial_object(self):
        body = stream("")
        self.assertIsNone(response_failure(body))
        self.assertEqual(message_response(body)["content"][0]["input"], {})

    def test_nonempty_arguments_are_decoded(self):
        self.assertEqual(message_response(stream('{"camera":"head"}'))["content"][0]["input"], {"camera": "head"})

    def test_malformed_or_incomplete_stream_still_rejected(self):
        self.assertIsNotNone(response_failure(stream('{"camera":')))
        self.assertIsNotNone(response_failure(stream("", stop=False)))


if __name__ == "__main__":
    unittest.main()
