"""Adapt pinned CALVIN PyBullet state, images and task predicates."""
from copy import deepcopy
import time

import numpy as np

from uni_robot_agent.services.environment.contracts import (
    ArmDescription, ArmState, CameraDescription, EnvironmentDescription,
    EnvironmentState, Pose, PoseCommand, RequestRejected, StepReceipt,
)
from uni_robot_agent.services.environment.snapshots import CameraFrame, CapturedObservation


def camera_frame(camera, observation, depth_enabled):
    # Native static and wrist camera classes use different attribute names.
    if camera.name == "static":
        view, projection = camera.viewMatrix, camera.projectionMatrix
    else:
        view, projection = camera.view_matrix, camera.projection_matrix
    view = np.asarray(view).reshape(4, 4, order="F")
    projection = np.asarray(projection).reshape(4, 4, order="F")
    width, height = camera.width, camera.height
    intrinsic = np.array([[projection[0, 0] * width / 2, 0, width / 2],
                          [0, projection[1, 1] * height / 2, height / 2], [0, 0, 1]])
    # OpenGL looks down -Z with +Y up; public optical coordinates use +Z/+Y down.
    world_from_optical = np.linalg.inv(view) @ np.diag([1., -1., -1., 1.])
    rgb = np.asarray(observation["rgb_obs"]["rgb_" + camera.name]).copy()
    depth = (np.asarray(observation["depth_obs"]["depth_" + camera.name], dtype=np.float32).copy()
             if depth_enabled else None)
    return CameraFrame(rgb, depth, intrinsic, world_from_optical)


class CalvinAdapter:
    def __init__(self, env, oracle, *, task, instruction, episode_id, metadata,
                 observation, max_steps=360, depth_enabled=False):
        self.env, self.oracle, self.task = env, oracle, task
        self.instruction, self.episode_id, self.metadata = instruction, episode_id, metadata
        self.max_steps, self.depth_enabled = max_steps, depth_enabled
        self.steps, self.success, self.finished, self.closed = 0, False, False, False
        self.outcome = None
        self.start_info = deepcopy(env.get_info())
        self.cameras = {camera.name: camera for camera in env.cameras}
        if set(self.cameras) != {"static", "gripper"}:
            raise ValueError("CALVIN requires the native static and gripper cameras")
        self.description = EnvironmentDescription(
            arms=[ArmDescription(name="arm", control_frame="world", rotation_radius_m=.15,
                grasp_site_description="Native Panda TCP between the longer finger pads; binary opening, 0 closed and 1 open.")],
            cameras=[CameraDescription(name=name, has_depth=depth_enabled) for name in self.cameras],
            control_frequency_hz=float(env.control_freq), max_episode_steps=max_steps,
            observation_interval_steps=1,
            metadata={"control_constraints": "World-frame absolute TCP poses; gripper targets below 0.5 close, otherwise open.",
                      "evaluation_protocol": "single_task", "observation_source": "native_calvin"})
        from uni_robot_agent.integrations.robot_resources import add_robot_resources, urdf_kinematics
        robot = env.robot
        model = urdf_kinematics(robot.filename)
        model["control"] = {"base_position": list(robot.base_position),
            "base_quaternion_wxyz": [robot.base_orientation[3], *robot.base_orientation[:3]],
            "tcp_link_id": robot.tcp_link_id, "arm_joint_ids": list(robot.arm_joint_ids),
            "gripper_joint_ids": list(robot.gripper_joint_ids), "gripper_joint_limits": list(robot.gripper_joint_limits),
            "opening": "Binary: normalized targets below 0.5 close; otherwise open."}
        add_robot_resources(self.description, model=model)
        self.cache(observation)

    def episode_identity(self):
        return self.instruction, self.metadata

    def cache(self, observation):
        # Scene state and oracle inputs remain private to the native service.
        self.frames = {name: camera_frame(camera, observation, self.depth_enabled)
                       for name, camera in self.cameras.items()}

    def read_state(self):
        robot, bullet = self.env.robot, self.env.p
        position, quaternion = bullet.getLinkState(robot.robot_uid, robot.tcp_link_id,
                                                   physicsClientId=self.env.cid)[:2]
        joints = bullet.getJointStates(robot.robot_uid, robot.arm_joint_ids, physicsClientId=self.env.cid)
        fingers = bullet.getJointStates(robot.robot_uid, robot.gripper_joint_ids, physicsClientId=self.env.cid)
        low, high = robot.gripper_joint_limits
        opening = (sum(j[0] for j in fingers) / len(fingers) - low) / (high - low)
        arm = ArmState(control_pose=Pose(position=list(position), quaternion=[quaternion[3], *quaternion[:3]]),
                       joints=[j[0] for j in joints], gripper=float(np.clip(opening, 0, 1)),
                       gripper_command=float(robot.gripper_action > 0))
        return EnvironmentState(arms={"arm": arm}, state_version=self.steps, native_steps=self.steps,
                                ended=self.finished or self.success or self.steps >= self.max_steps)

    def validate_commands(self, commands, mode):
        if mode != "pose":
            raise RequestRejected("CALVIN Direct accepts pose commands only")
        for command in commands:
            if not isinstance(command, PoseCommand) or command.solver != "native" or set(command.arms) != {"arm"}:
                raise RequestRejected("Supply one native-solver pose and opening for arm")

    def step_pose(self, command):
        self.validate_commands([command], "pose")
        if self.closed or self.read_state().ended:
            raise RequestRejected("Episode has ended")
        arm = command.arms["arm"]
        q = arm.pose.quaternion
        # The pinned native API accepts an absolute (position, xyzw, +/-1) tuple.
        action = (list(arm.pose.position), [*q[1:], q[0]], 1 if arm.gripper >= .5 else -1)
        observation, _, _, info = self.env.step(action)
        self.steps += 1
        self.success = bool(self.oracle.get_task_info_for_set(self.start_info, info, {self.task}))
        self.cache(observation)
        return StepReceipt(accepted=True, state=self.read_state(), diagnostics={"executed_steps": 1})

    def step_joints(self, command):
        raise RequestRejected("Joint control is unavailable")

    def step_native(self, command):
        raise RequestRejected("Raw action control is unavailable")

    def preview_joints(self, commands):
        raise RequestRejected("Joint previews are unavailable")

    def capture(self, cameras=None, *, high_resolution=False):
        names = list(self.cameras) if cameras is None else cameras
        if not names or len(names) != len(set(names)) or set(names) - set(self.cameras):
            raise RequestRejected("Select unique configured cameras")
        state = self.read_state()
        return CapturedObservation(self.episode_id, self.steps, time.time(), state.model_dump(mode="json"),
                                   {name: self.frames[name] for name in names}, terminated=state.ended)

    def finish(self, reason, status=None):
        if self.outcome is None:
            self.finished = True
            self.outcome = {"task_success": self.success, "reason": reason, "native_steps": self.steps,
                            "benchmark": "calvin", "evaluation_protocol": "single_task"}
        return dict(self.outcome)

    def close(self):
        if not self.closed:
            self.closed = True
            self.env.close()
