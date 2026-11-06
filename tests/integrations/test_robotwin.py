import unittest
from types import SimpleNamespace

import numpy as np

from uni_robot_agent.integrations.robotwin.adapter import (
    ARMS, CAMERAS, RoboTwinAdapter, measured_arm, native_ee_action,
)
from uni_robot_agent.services.environment.contracts import ArmCommand, Pose, PoseCommand, RequestRejected


def command():
    return PoseCommand(arms={
        'left': ArmCommand(pose=Pose(position=[1, 2, 3], quaternion=[1, 0, 0, 0]), gripper=.2),
        'right': ArmCommand(pose=Pose(position=[4, 5, 6], quaternion=[2**-.5, 0, 0, 2**-.5]), gripper=.8),
    })


class Native:
    def __init__(self):
        self.step_lim, self.take_action_cnt = 2, 0
        self.eval_success, self.closes, self.captures = False, 0, 0
        self.robot = SimpleNamespace()
        for name in ARMS:
            joints = [object(), object()]
            setattr(self.robot, name + '_entity', SimpleNamespace(
                get_active_joints=lambda j=joints: j, get_qpos=lambda: np.array([.3, .5])))
            setattr(self.robot, name + '_arm_joints', joints[:1])
            setattr(self.robot, name + '_gripper', [(joints[1], 2., .1)])
            setattr(self.robot, name + '_gripper_scale', [0., 1.])
            setattr(self.robot, 'get_' + name + '_tcp_pose', lambda: [1, 2, 3, 1, 0, 0, 0])
            setattr(self.robot, 'get_' + name + '_gripper_val', lambda: .9)

    def get_obs(self):
        self.captures += 1
        transform = np.eye(4)
        transform[:3, 3] = [-1, -2, -3]
        return {'observation': {name: {'rgb': np.full((2, 3, 3), self.take_action_cnt, dtype=np.uint8),
            'depth': np.full((2, 3), 1250, dtype=np.uint16), 'intrinsic_cv': np.eye(3),
            'extrinsic_cv': transform} for name in CAMERAS}, 'privileged_object_pose': [99, 99, 99]}

    def take_action(self, action, *, action_type):
        self.last_action, self.action_type = action, action_type
        self.take_action_cnt += 1

    def close_env(self):
        self.closes += 1


def adapter(env):
    return RoboTwinAdapter(env, episode_id='test', instruction='place the blocks', metadata={}, depth_enabled=True)


class RoboTwinTests(unittest.TestCase):
    def test_rotated_tcp_offset_and_arm_order(self):
        action = native_ee_action(command().arms)
        np.testing.assert_allclose(action[:3], [.88, 2, 3])
        np.testing.assert_allclose(action[8:11], [4, 4.88, 6])
        np.testing.assert_allclose(action[3:7], [1, 0, 0, 0])
        self.assertEqual((action[7], action[15]), (.2, .8))

    def test_state_uses_measurement_not_drive_target(self):
        state = measured_arm(Native().robot, 'left')
        self.assertAlmostEqual(state.gripper, .2)
        self.assertEqual(state.gripper_command, .9)
        self.assertEqual(state.joints, [.3])

    def test_capture_units_transform_privacy_and_no_physics(self):
        env = Native()
        binding = adapter(env)
        snapshot = binding.capture()
        frame = snapshot.cameras['head_camera']
        np.testing.assert_allclose(frame.depth_m, 1.25)
        np.testing.assert_allclose(frame.T_world_from_optical[:3, 3], [1, 2, 3])
        self.assertNotIn('privileged_object_pose', snapshot.state)
        binding.capture()
        self.assertEqual((env.take_action_cnt, env.captures), (0, 1))
        with self.assertRaises(RequestRejected):
            binding.capture(['head_camera', 'head_camera'])

    def test_budget_stops_actions_and_submission_cannot_claim_success(self):
        env = Native()
        binding = adapter(env)
        binding.step_pose(command())
        self.assertFalse(binding.read_state().ended)
        binding.step_pose(command())
        self.assertTrue(binding.read_state().ended)
        self.assertEqual(env.action_type, 'ee')
        with self.assertRaises(RequestRejected):
            binding.step_pose(command())
        self.assertEqual(env.take_action_cnt, 2)
        self.assertFalse(binding.finish('submit', 'success')['task_success'])
        self.assertEqual(binding.finish('submit'), binding.finish('again'))
        binding.close()
        binding.close()
        self.assertEqual(env.closes, 1)

    def test_invalid_and_unsupported_actions_do_not_step(self):
        env = Native()
        binding = adapter(env)
        one_arm = command()
        del one_arm.arms['right']
        with self.assertRaises(RequestRejected):
            binding.step_pose(one_arm)
        with self.assertRaises(RequestRejected):
            binding.step_native([0] * 16)
        self.assertEqual(env.take_action_cnt, 0)

    def test_http_receipts_reject_replay_and_stale_state(self):
        import tempfile
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from uni_robot_agent.artifacts.store import ArtifactStore
        from uni_robot_agent.services.environment.contracts import EpisodeInfo
        from uni_robot_agent.services.environment.owner import OwnedAdapter
        from uni_robot_agent.services.environment.routes import build_router
        from uni_robot_agent.services.environment.service import EnvironmentService

        env = Native()
        with tempfile.TemporaryDirectory() as directory:
            owned = OwnedAdapter(lambda: adapter(env))
            service = EnvironmentService(owned, EpisodeInfo(episode_id='test', task='place blocks',
                integration='robotwin', environment=owned.description), ArtifactStore(directory))
            app = FastAPI()
            app.include_router(build_router(service))
            try:
                service.initialize()
                with TestClient(app) as client:
                    body = {'identity': {'episode_id': 'test', 'request_id': 'move', 'sequence': 1},
                            'payload': {'command': command().model_dump(mode='json'), 'expected_state_version': 0}}
                    reply = client.post('/steps', json=body)
                    self.assertEqual(reply.status_code, 200, reply.text)
                    self.assertNotEqual(client.post('/steps', json=body).status_code, 200)
                    self.assertEqual(env.take_action_cnt, 1)
                    self.assertEqual(client.get('/requests/move').status_code, 200)
                    body['identity'] = {'episode_id': 'test', 'request_id': 'stale', 'sequence': 2}
                    self.assertNotEqual(client.post('/steps', json=body).status_code, 200)
                    self.assertEqual(env.take_action_cnt, 1)
                    self.assertEqual(client.get('/snapshots/latest').json()['state_version'], 1)
                    self.assertEqual(client.post('/previews', json={
                        'identity': {'episode_id': 'test', 'request_id': 'preview', 'sequence': 3},
                        'payload': {'commands': [{'kind': 'joints', 'arms': {}}], 'expected_state_version': 1}
                    }).status_code, 422)
            finally:
                service.close()
        self.assertEqual(env.closes, 1)

    def test_native_success_survives_agent_give_up(self):
        env = Native()
        binding = adapter(env)
        env.eval_success = True
        self.assertTrue(binding.read_state().ended)
        self.assertTrue(binding.finish('submitted', 'give_up')['task_success'])
        self.assertEqual(binding.read_state().metadata, {})


if __name__ == '__main__':
    unittest.main()
