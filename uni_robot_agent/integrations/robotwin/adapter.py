"""Translate public TCP targets into pinned RoboTwin EE actions."""
import time

import numpy as np
from scipy.spatial.transform import Rotation

from uni_robot_agent.services.environment.contracts import (
    ArmCommand, ArmDescription, ArmState, CameraDescription, EnvironmentDescription,
    EnvironmentState, Pose, PoseCommand, RequestRejected, StepReceipt,
)
from uni_robot_agent.services.environment.snapshots import CameraFrame, CapturedObservation

ARMS = ("left", "right")
CAMERAS = ("head_camera", "left_camera", "right_camera")


def native_ee_action(commands):
    """RoboTwin's EE reference is 0.12 m behind its TCP along local X."""
    action = []
    for name in ARMS:
        target = commands[name]
        q = np.asarray(target.pose.quaternion)
        rotation = Rotation.from_quat(q[[1, 2, 3, 0]])
        position = np.asarray(target.pose.position) - rotation.apply([.12, 0., 0.])
        action.extend([*position, *q, target.gripper])
    return np.asarray(action, dtype=float)


def measured_arm(robot, name):
    """Use articulation qpos, not RoboTwin's cached gripper drive targets."""
    entity = getattr(robot, name + "_entity")
    joints, qpos = entity.get_active_joints(), entity.get_qpos()
    joint, multiplier, offset = getattr(robot, name + "_gripper")[0]
    closed, opened = getattr(robot, name + "_gripper_scale")
    if multiplier == 0 or opened == closed:
        raise ValueError("The native gripper has no invertible scalar opening")
    opening = ((float(qpos[joints.index(joint)]) - offset) / multiplier - closed) / (opened - closed)
    tcp = getattr(robot, "get_" + name + "_tcp_pose")()
    arm_joints = getattr(robot, name + "_arm_joints")
    return ArmState(control_pose=Pose(position=list(tcp[:3]), quaternion=list(tcp[3:])),
                    joints=[float(qpos[joints.index(j)]) for j in arm_joints],
                    gripper=float(np.clip(opening, 0., 1.)),
                    gripper_command=float(getattr(robot, "get_" + name + "_gripper_val")()))


def camera_frames(observation, names, depth_enabled):
    result = {}
    for name in names:
        item = observation["observation"][name]
        # Native depth is millimetres; extrinsic_cv maps world to optical.
        depth = np.asarray(item["depth"], dtype=np.float32) / 1000. if depth_enabled else None
        # SAPIEN exposes OpenCV [R|t] as 3x4, without the homogeneous row.
        world_to_optical = np.eye(4)
        world_to_optical[:3] = np.asarray(item["extrinsic_cv"])
        result[name] = CameraFrame(np.asarray(item["rgb"]).copy(), depth,
                                  np.asarray(item["intrinsic_cv"]).copy(),
                                  np.linalg.inv(world_to_optical))
    return result


class RoboTwinAdapter:
    def __init__(self, env, *, episode_id, instruction, metadata, depth_enabled, cameras=CAMERAS):
        self.env, self.episode_id = env, episode_id
        self.instruction, self.metadata = instruction, metadata
        self.depth_enabled = depth_enabled
        self.camera_names = tuple(cameras)
        self.closed, self.finished, self.outcome = False, False, None
        self.obs = self.observe()
        if set(self.camera_names) - self.obs["observation"].keys():
            raise ValueError("The native observation is missing a configured camera")
        # Validate embodiment geometry before exposing the episode service.
        for name in ARMS:
            measured_arm(env.robot, name)
        self.description = EnvironmentDescription(
            arms=[ArmDescription(name=name, control_frame="world", rotation_radius_m=.15,
                grasp_site_description="Native gripper TCP centre, local X approach axis; normalized scalar opening.") for name in ARMS],
            cameras=[CameraDescription(name=name, has_depth=depth_enabled) for name in self.camera_names],
            control_frequency_hz=30.0, max_episode_steps=int(env.step_lim), observation_interval_steps=1,
            metadata={"control_constraints": "Both arms receive coordinated absolute TCP poses and scalar openings.",
                      "step_unit": "native_policy_action", "native_action_type": "ee",
                      "control_timing": "30 Hz is the upstream nominal policy rate; native trajectory duration varies."},
        )

    def episode_identity(self):
        return self.instruction, self.metadata

    def observe(self):
        return self.env.get_obs()

    def read_state(self):
        steps = int(self.env.take_action_cnt)
        return EnvironmentState(arms={name: measured_arm(self.env.robot, name) for name in ARMS},
            state_version=steps, native_steps=steps,
            ended=bool(self.finished or self.env.eval_success or steps >= self.env.step_lim))

    def validate_commands(self, commands, mode):
        if mode != "pose":
            raise RequestRejected("RoboTwin Direct accepts pose commands only")
        for command in commands:
            if (not isinstance(command, PoseCommand) or command.solver != "native"
                    or set(command.arms) != set(ARMS)
                    or not all(isinstance(value, ArmCommand) for value in command.arms.values())):
                raise RequestRejected("Supply native-solver poses and openings for both arms")
            native_ee_action(command.arms)

    def step_pose(self, command):
        self.validate_commands([command], "pose")
        if self.closed or self.read_state().ended:
            raise RequestRejected("Episode has ended")
        before = int(self.env.take_action_cnt)
        # One upstream EE action owns its native planning/servo execution. Shared
        # gripper tools still own waypoint composition and tool termination.
        self.env.take_action(native_ee_action(command.arms), action_type="ee")
        if self.env.take_action_cnt != before + 1:
            raise RuntimeError("Native action did not advance exactly one policy action")
        self.obs = self.observe()
        return StepReceipt(accepted=True, state=self.read_state(), diagnostics={"executed_steps": 1})

    def step_joints(self, command):
        raise RequestRejected("Joint control is not exposed by this Direct binding")

    def step_native(self, command):
        raise RequestRejected("Raw action control is not exposed by this Direct binding")

    def preview_joints(self, commands):
        raise RequestRejected("Joint previews are unavailable")

    def capture(self, cameras=None, *, high_resolution=False):
        names = list(self.camera_names) if cameras is None else cameras
        if not names or len(names) != len(set(names)) or set(names) - set(self.camera_names):
            raise RequestRejected("Select unique configured cameras")
        state = self.read_state()
        return CapturedObservation(self.episode_id, state.state_version, time.time(),
            state.model_dump(mode="json"), camera_frames(self.obs, names, self.depth_enabled), terminated=state.ended)

    def finish(self, reason, status=None):
        if self.outcome is None:
            self.finished = True
            self.outcome = {"task_success": bool(self.env.eval_success), "reason": reason,
                            "native_steps": int(self.env.take_action_cnt), "step_unit": "native_policy_action"}
        return dict(self.outcome)

    def close(self):
        if not self.closed:
            self.closed = True
            self.env.close_env()
