"""CPU receipt checks: real client/encoding, inert HTTP and no physical work."""
from contextlib import contextmanager, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from uni_robot_agent.context.code_as_policy import prepare_code_policy
from uni_robot_agent.context.prompt import Prompt, PromptContext, assemble_prompt
from uni_robot_agent.execution import ExecutionResult
from uni_robot_agent.execution.code_as_policy import CodeResult, Robot, ToolCallError
from uni_robot_agent.services.mcp.results import encode_call_result
from uni_robot_agent.tools.results import ArmFeedback, MotionFeedback, ResultFields, ResultState
from uni_robot_agent.tools.toolkit import ImageAttachment, ToolOutput


def motion_output():
    return ToolOutput(extra={
        "state": {"state_version": 7, "native_steps": 48, "ended": False,
                  "arms": {"right": {"gripper_command": 0.0}}},
        "remaining_steps": 52, "snapshot_id": "snapshot-7",
        "feedback": {
            "pose_reached": False, "targets_reached": False,
            "steps_executed": 0, "executed_native_steps": 0, "no_execution": True,
            "stop_reason": "planning_rejected",
            "stop_evidence": {"arm": "left", "reason": "joint_limit"},
            "arms": {
                "right": {"pose_reached": True, "position_error_m": .001,
                          "orientation_error_rad": .02, "opening_reached": None,
                          "opening_status": "not_requested", "future_diagnostic": [1, 2]},
                "left": {"pose_reached": False, "position_error_m": .07},
            },
        },
        "image_cameras": ["scene", "right_wrist"],
    }, images=[ImageAttachment(b"scene bytes", name="scene"),
               ImageAttachment(b"wrist bytes", name="right_wrist")])


@contextmanager
def connected(output, *, disconnect=False):
    requests = []
    wire = encode_call_result(ExecutionResult.completed(output))
    wire["_meta"] = {"uni_robot_agent/call_id": "call-7"}

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        if body["method"] == "initialize":
            result = {}
        elif body["method"] == "tools/list":
            result = {"tools": [{"name": "move"}]}
        else:
            if disconnect:
                raise httpx.ReadError("lost acknowledgement", request=request)
            result = wire
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    client = httpx.Client(transport=httpx.MockTransport(handle))
    with tempfile.TemporaryDirectory() as directory:
        with patch("uni_robot_agent.execution.code_as_policy.client.httpx.Client", return_value=client):
            with Robot("http://receipt.test/mcp", image_dir=directory) as robot:
                yield robot, requests, wire


class ReceiptTests(unittest.TestCase):
    def test_completed_client_preserves_wire_images_and_no_extra_calls(self):
        output = motion_output()
        original = deepcopy(output.to_dict(for_model=True))
        encoded = encode_call_result(ExecutionResult.completed(output))
        with connected(output) as (robot, requests, wire):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                result = robot.tools.move(targets=[{"arm": "right", "pose": [0] * 6}])
                summary = json.loads(result.summary())
                self.assertIs(result.feedback.arms["right"].pose_reached, True)
                self.assertIs(result.feedback.arms["left"].pose_reached, False)
                result.summary()
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(len(requests), 3)  # Initialize, discovery, one action.
            self.assertEqual(result.data, original)
            self.assertEqual([path.read_bytes() for path in result.images], [b"scene bytes", b"wrist bytes"])
            self.assertEqual(summary["images"], [str(path) for path in result.images])
            self.assertEqual(summary["image_cameras"], ["scene", "right_wrist"])
            self.assertEqual(summary["call_id"], "call-7")
            self.assertEqual(json.loads(wire["content"][-1]["text"]), original)
        self.assertEqual(encode_call_result(ExecutionResult.completed(output)), encoded)

    def test_planning_rejection_is_valid_call_not_success_or_exception(self):
        with connected(motion_output()) as (robot, _, wire):
            result = robot.tools.move()
            self.assertIs(wire["isError"], False)
            self.assertIs(result.call_ok, True)
            self.assertIs(result.feedback.pose_reached, False)
            self.assertIs(result.feedback.targets_reached, False)
            self.assertIs(result.feedback.no_execution, True)
            summary = json.loads(result.summary())
            self.assertEqual(summary["feedback"]["steps_executed"], 0)
            self.assertEqual(summary["feedback"]["executed_native_steps"], 0)
            self.assertEqual(summary["feedback"]["stop_evidence"]["arm"], "left")
            self.assertIsNone(summary["feedback"]["arms"]["right"]["opening_reached"])
            self.assertNotIn("success", summary)
            self.assertNotIn("blocked", summary["feedback"])

    def test_partial_execution_error_keeps_receipt(self):
        output = motion_output()
        output.error = "Observation failed after motion"
        output.extra["feedback"].update(steps_executed=2, executed_native_steps=4, no_execution=False)
        output.extra["artifact_error"] = "camera unavailable"
        with connected(output) as (robot, requests, _):
            with self.assertRaises(ToolCallError) as raised:
                robot.tools.move()
            result = raised.exception.result
            self.assertIs(result.call_ok, False)
            self.assertEqual(result.error, output.error)
            self.assertEqual(result.feedback.steps_executed, 2)
            self.assertEqual(result.artifact_error, "camera unavailable")
            self.assertEqual(json.loads(result.summary())["artifact_error"], "camera unavailable")
            self.assertEqual(len(requests), 3)

    def test_unknown_and_malformed_fields_are_not_coerced(self):
        raw = {"call_ok": "true", "remaining_steps": False, "snapshot_id": 7,
               "state": {"ended": "false", "native_steps": 0},
               "feedback": {"pose_reached": 1, "steps_executed": "2",
                            "arms": {"right": {"pose_reached": False, "opening_reached": "true"}}},
               "custom": {"anything": [1, 2]}}
        original = deepcopy(raw)
        result = CodeResult(raw, (), "partial")
        self.assertIsNone(result.call_ok)
        self.assertIsNone(result.remaining_steps)
        self.assertIsNone(result.snapshot_id)
        self.assertIsNone(result.state.ended)
        self.assertEqual(result.state.native_steps, 0)
        self.assertIsNone(result.feedback.pose_reached)
        self.assertIsNone(result.feedback.steps_executed)
        self.assertIsNone(result.feedback.arms["right"].opening_reached)
        self.assertIs(result.feedback.arms["right"].pose_reached, False)
        self.assertIn("custom", json.loads(result.summary())["data_fields"])
        self.assertEqual(result.data, original)
        for value in (None, [], "unexpected"):
            with self.subTest(value=value):
                result = CodeResult({"feedback": value, "state": value}, (), "")
                self.assertIsNone(result.feedback)
                self.assertIsNone(result.state)
                result.summary()

    def test_scene_device_hybrid_and_custom_payloads_remain_available(self):
        payloads = [
            {"state": {"state_version": 3, "native_steps": 0, "ended": False}, "snapshot_id": "scene-3"},
            {"state": {"kind": "devices", "state_version": 9, "ended": True, "devices": {"base": {}}},
             "execution": {"acknowledged": 1}, "assessment": {"status": "reviewed"}},
            {"content": "skill text", "sha256": "skill-hash"},
            {"points": [[.1, .2, .3]], "calibration": {"camera": "scene"}},
            {},
        ]
        for payload in payloads:
            with self.subTest(payload=payload):
                raw = deepcopy(payload)
                result = CodeResult(payload, (), "generic")
                self.assertIsNone(result.feedback)
                self.assertIsNone(result.remaining_steps)
                self.assertNotIn("feedback", json.loads(result.summary()))
                self.assertEqual(result.data, raw)
        device = CodeResult(payloads[1], (), "device")
        self.assertIsNone(device.state.native_steps)
        self.assertIs(device.state.ended, True)
        self.assertEqual(device.state.state_version, 9)

    def test_exhausted_budget_and_end_are_not_lost(self):
        result = CodeResult({"remaining_steps": 0, "state": {"ended": True, "native_steps": 600}}, (), "end")
        self.assertEqual(result.remaining_steps, 0)
        self.assertIs(result.state.ended, True)
        self.assertEqual(json.loads(result.summary())["remaining_steps"], 0)

    def test_lost_acknowledgement_is_not_replayed(self):
        with connected(motion_output(), disconnect=True) as (robot, requests, _):
            with self.assertRaisesRegex(RuntimeError, "Execution may have occurred"):
                robot.tools.move()
            with self.assertRaisesRegex(RuntimeError, "closed"):
                robot.tools.move()
            self.assertEqual(sum(row["method"] == "tools/call" for row in requests), 1)

    def test_generated_docs_cover_types_without_changing_input_schema(self):
        schemas = [{"name": "observe", "description": "Read a scene", "input_schema": {"type": "object"}}]
        original = deepcopy(schemas)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prompt = prepare_code_policy(schemas, root, Prompt(user="Inspect"))
            document = (root / "scratch/robot_api.md").read_text()
            for model in (ResultFields, ResultState, MotionFeedback, ArmFeedback):
                for name, field in model.model_fields.items():
                    self.assertIn(f"| `{name}` |", document)
                    self.assertIn(field.description, document)
            self.assertIn(json.dumps(schemas[0]["input_schema"], indent=2), document)
            self.assertIn("result.summary()", document)
            self.assertIn(str(root / "scratch/robot_api.md"), prompt.system)
            self.assertEqual((root / "effective_prompt.md").read_text(), prompt.flatten())
        self.assertEqual(schemas, original)

    def test_guidance_is_only_added_to_code_policy_prompt(self):
        base = Prompt(user="Inspect")
        regular = assemble_prompt(base, PromptContext(integration_name="test", task="Inspect"))
        cap = assemble_prompt(base, PromptContext(integration_name="test", task="Inspect", interaction_mode="code_as_policy"))
        self.assertNotIn("result.summary()", regular.flatten())
        self.assertIn("result.summary()", cap.flatten())


if __name__ == "__main__":
    unittest.main()
