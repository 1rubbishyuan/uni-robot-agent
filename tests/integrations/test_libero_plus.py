import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from uni_robot_agent.integrations.libero_plus.adapter import LiberoAdapter, observation_frames
from uni_robot_agent.services.environment.contracts import ArmCommand, ArmState, Pose, PoseCommand, RequestRejected


POSE = Pose(position=(0., 0., .5), quaternion=(1., 0., 0., 0.))


class Binding:
    constraint = "world pose"

    def __init__(self, *args):
        pass

    def encode(self, targets):
        return [*targets["arm"].pose.position, 0, 0, 0, 1]

    def read(self, commands):
        return {"arm": ArmState(control_pose=POSE, gripper=.4, gripper_command=commands["arm"])}


class Native:
    noise = 0

    def __init__(self):
        self.calls = self.closes = 0
        self.succeeds = False
        self.sim = SimpleNamespace()

    def step(self, action):
        self.calls += 1
        return {"native_image": self.calls}, 0, False, {"privileged": "hidden"}

    def check_success(self):
        return self.succeeds

    def close(self):
        self.closes += 1


class LiberoTests(unittest.TestCase):
    def adapter(self, env, budget=2):
        with patch("uni_robot_agent.integrations.libero_plus.adapter.OSC", Binding):
            return LiberoAdapter(env, {}, episode_id="test", instruction="move the cube",
                metadata={"benchmark": "libero_plus", "init_state_index": 0}, max_steps=budget,
                depth_enabled=False, convention="opengl")

    def test_budget_success_and_submission_are_distinct(self):
        env = Native()
        adapter = self.adapter(env)
        command = PoseCommand(arms={"arm": ArmCommand(pose=POSE, gripper=0.)})
        adapter.step_pose(command)
        self.assertEqual(adapter.obs, {"native_image": 1})
        self.assertFalse(adapter.read_state().ended)
        adapter.step_pose(command)
        self.assertTrue(adapter.read_state().ended)
        with self.assertRaises(RequestRejected):
            adapter.step_pose(command)
        self.assertEqual(env.calls, 2)
        self.assertFalse(adapter.finish("submitted", "success")["task_success"])
        self.assertEqual(adapter.finish("again"), adapter.finish("submitted"))
        adapter.close()
        adapter.close()
        self.assertEqual(env.closes, 1)

    def test_native_success_stops_without_exposing_verdict_in_state(self):
        env = Native()
        adapter = self.adapter(env)
        env.succeeds = True
        receipt = adapter.step_pose(PoseCommand(arms={"arm": ArmCommand(pose=POSE, gripper=1.)}))
        self.assertTrue(receipt.state.ended)
        self.assertEqual(receipt.state.metadata, {})
        self.assertTrue(adapter.finish("agent_exit", "give_up")["task_success"])

    def test_invalid_command_cannot_advance_physics(self):
        env = Native()
        adapter = self.adapter(env)
        with self.assertRaises(RequestRejected):
            adapter.step_pose(PoseCommand(arms={"wrong": ArmCommand(pose=POSE, gripper=1.)}))
        self.assertEqual(env.calls, 0)

    def test_corrupted_observations_are_preserved_and_flipped_with_depth(self):
        module = ModuleType("robosuite.utils.camera_utils")
        module.get_camera_intrinsic_matrix = lambda *a: np.eye(3)
        module.get_camera_extrinsic_matrix = lambda *a: np.eye(4)
        module.get_real_depth_map = lambda sim, value: value * 2
        rgb = np.arange(18, dtype=np.uint8).reshape(2, 3, 3)
        depth = np.arange(6).reshape(2, 3, 1)
        obs = {"agentview_image": rgb, "agentview_depth": depth, "object_pose": [99, 99, 99]}
        with patch.dict(sys.modules, {"robosuite.utils.camera_utils": module}):
            frame = observation_frames(obs, None, ["agentview"], depth_enabled=True, convention="opengl")["agentview"]
            np.testing.assert_array_equal(frame.rgb, rgb[::-1])
            np.testing.assert_array_equal(frame.depth_m, depth[:, :, 0][::-1] * 2)
            distorted = observation_frames(obs, None, ["agentview"], depth_enabled=True,
                convention="opencv", distorted_agentview=True)["agentview"]
        np.testing.assert_array_equal(distorted.rgb, rgb)
        self.assertIsNone(distorted.depth_m)
        self.assertIsNone(distorted.intrinsic_K)
        self.assertIsNone(distorted.T_world_from_optical)


if __name__ == "__main__":
    unittest.main()
