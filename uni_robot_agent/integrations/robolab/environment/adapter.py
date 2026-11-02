"""DROID native primitives behind the shared prepared-environment protocol."""
import json
import time
from pathlib import Path

import numpy as np
from PIL import Image

from uni_robot_agent.services.environment.contracts import (
    ArmCommand, ArmDescription, ArmState, CameraDescription, EnvironmentDescription,
    EnvironmentState, JointCommand, Pose, PoseCommand, RequestRejected, StepReceipt,
)
from uni_robot_agent.services.environment.snapshots import CameraFrame, CapturedObservation

from .controller import Controller
from .gripper import MAX_OPENING_M, NATIVE_ANGLE_SCALE
from .kinematics import MotionRejected, pose, pose_fields
from .native_compat import array


class RoboLabAdapter:
    def __init__(self, env, model, *, episode_id, metadata, output_dir, owner,
                 make_action=None, end_episode=None, refresh_images=None, record_frame=None,
                 robot_state=None, camera_state=None):
        if env.num_envs != 1 or env.action_manager.total_action_dim != 8:
            raise ValueError("RoboLab requires exactly one DROID with eight native action dimensions")
        self.env, self.model, self.owner = env, model, owner
        self.episode_id, self.metadata = episode_id, dict(metadata)
        self.output_dir = Path(output_dir)
        self.controller = Controller(model, env.step_dt)
        self._make_action = make_action or self._torch_action
        self._end_episode = end_episode or (lambda current: None)
        self._refresh_images = refresh_images or (lambda observation: observation)
        self._record_frame = record_frame or (lambda observation: None)
        self._robot_state = robot_state or self._native_robot_state
        self._camera_state = camera_state or self._native_camera_state
        self._obs = None
        self._steps = 0
        self._fault = None
        self._finished = self._closed = self._finalization_started = False
        self._reset_started = False
        self._result = None
        self._opening_command = 1.

    def reset(self):
        return self.owner.run(self._reset)

    def _reset(self):
        if self._reset_started:
            raise RuntimeError("One prepared episode per RoboLab service; reset cannot be replayed")
        self._reset_started = True
        self.env.reset_eval_state()
        self._obs, _ = self.env.reset()
        self._obs = self._refresh_images(self._obs)
        recorder = self.env.recorder_manager
        if recorder is not None:
            recorder.set_hdf5_file("run_0.hdf5")
            recorder.set_episode_index(0, env_ids=[0])
        self._record_frame(self._obs)
        measured = self._robot_state()
        self._opening_command = float(np.clip(measured["gripper_opening_m"] / MAX_OPENING_M, 0, 1))
        # Keep the actual initial drive angle rather than inferring it from pad deflection.
        self.controller.previous_drive = measured["finger_joint_rad"] / NATIVE_ANGLE_SCALE
        minimum = float(self.model.gripper.public["reachable_opening_m"][0])
        self.description = EnvironmentDescription(
            arms=[ArmDescription(name="right", control_frame="droid_robotiq_base_link",
                grasp_site_description="Robotiq base_link mounting flange in world coordinates; not the pad midpoint or eef_frame.",
                rotation_radius_m=.2, joint_names=self.model.arm_names)],
            cameras=[CameraDescription(name=name, has_depth=False,
                role="wrist" if "wrist" in name else "scene") for name in self._obs.get("image_obs", {})],
            control_frequency_hz=1. / self.controller.dt,
            max_episode_steps=int(self.env.max_episode_length),
            metadata={"observation_mode": "rgb_and_robot_proprioception",
                "gripper_opening_models": {"right": {"kind": "affine_pad_gap_v1", "command_offset_m": 0.,
                    "command_scale_m": MAX_OPENING_M, "closed_command": 0.,
                    "min_opening_m": minimum, "max_opening_m": MAX_OPENING_M}},
                "robot_manual": "DROID: Panda seven-joint arm and Robotiq 2F-85. "
                    "The controlled site is the mounting flange. Jaw opening is measured between actual pad surfaces; "
                    "drive targets persist under contact. Native joint travel bounds the reachable gap. "
                    "Drive gains are angular stiffness/damping and torque limits, not measured contact forces. "
                    "Collision hulls cover the robot only. Camera optical axes are +x right, +y down, +z forward.",
                "artifact_payloads": {"robot_calibration": {
                    **self.model.public, "drive_parameters": self.metadata.get("drive_parameters")}},
            },
        )
        return self._state()

    def _native_robot_state(self):
        robot = self.env.scene["robot"]
        ids, _ = robot.find_joints(self.model.arm_names, preserve_order=True)
        finger, _ = robot.find_joints("finger_joint")
        gripper = self.model.gripper
        bodies = [gripper.flange_body, *(body for body, _ in gripper.pads.values())]
        body_ids, _ = robot.find_bodies([body.rsplit("/", 1)[-1] for body in bodies], preserve_order=True)
        data = robot.data
        positions, quaternions = array(data.body_pos_w)[0], array(data.body_quat_w)[0]
        poses = {body: pose(positions[index], quaternions[index]) for body, index in zip(bodies, body_ids)}
        flange = pose_fields(poses[gripper.flange_body])
        return {**flange, "joints": array(data.joint_pos)[0, ids].tolist(),
                "joint_velocities": array(data.joint_vel)[0, ids].tolist(),
                "finger_joint_rad": float(array(data.joint_pos)[0, finger[0]]),
                "gripper_opening_m": gripper.measure(poses),
                "root_position": array(data.root_pos_w)[0].tolist(),
                "root_quaternion": array(data.root_quat_w)[0].tolist()}

    def _native_camera_state(self, name, original, returned):
        data = self.env.scene[name].data
        intrinsic = array(data.intrinsic_matrices)[0].copy()
        intrinsic[0, :] *= returned[0] / original[0]
        intrinsic[1, :] *= returned[1] / original[1]
        extrinsic = pose(array(data.pos_w)[0], array(data.quat_w_opengl)[0])
        extrinsic[:3, :3] = extrinsic[:3, :3] @ np.diag([1, -1, -1])
        return intrinsic, extrinsic

    def _torch_action(self, row):
        import torch
        return torch.as_tensor(row, dtype=torch.float32, device=self.env.device).reshape(1, 8)

    def _native_steps(self):
        return int(array(self.env.episode_length_buf)[0]) if hasattr(self.env, "episode_length_buf") else self._steps

    def read_state(self):
        return self.owner.run(self._state)

    def _state(self):
        if self._obs is None:
            raise RuntimeError("Native episode has not been prepared")
        raw = self._robot_state()
        return EnvironmentState(state_version=self._steps, native_steps=self._steps,
            ended=self._finished or self._fault is not None or bool(self.env.all_terminated)
                  or self._native_steps() >= int(self.env.max_episode_length),
            arms={"right": ArmState(control_pose=Pose(position=raw["position"], quaternion=raw["quaternion"]),
                joints=raw["joints"], gripper=float(np.clip(raw["gripper_opening_m"] / MAX_OPENING_M, 0, 1)),
                gripper_command=self._opening_command,
                metadata={"gripper_opening_m": raw["gripper_opening_m"],
                    "gripper_target_opening_m": self._opening_command * MAX_OPENING_M,
                    "gripper_applied_target_opening_m": float(np.interp(
                        self.controller.previous_drive, self.model.gripper.commands, self.model.gripper.widths)),
                    "gripper_joint_pos_rad": raw["finger_joint_rad"],
                    "joint_velocities_rad_s": raw["joint_velocities"],
                    "robot_root_world_pose": {"position": raw["root_position"], "quaternion": raw["root_quaternion"]}})},
        )

    def validate_commands(self, commands, mode):
        for command in commands:
            if isinstance(command, PoseCommand):
                valid = command.solver == "native" and all(isinstance(v, ArmCommand) for v in command.arms.values())
            else:
                valid = isinstance(command, JointCommand)
            if not valid or set(command.arms) != {"right"}:
                raise RequestRejected("RoboLab accepts native pose or joint targets for exactly the right arm")

    def step_pose(self, command):
        return self.owner.run(lambda: self._step(command))

    def step_joints(self, command):
        return self.owner.run(lambda: self._step(command))

    def _step(self, command):
        self.validate_commands([command], command.kind)
        if self._state().ended:
            raise RequestRejected("RoboLab episode has ended")
        target = command.arms["right"]
        measured = self._robot_state()
        try:
            if isinstance(command, PoseCommand):
                row = self.controller.plan_pose(pose(target.pose.position, target.pose.quaternion), target.gripper, measured)
            else:
                row = self.controller.plan_joints(target.joints, target.gripper, measured)
        except MotionRejected as error:
            return StepReceipt(accepted=False, state=self._state(),
                               diagnostics={"reason": "ik_candidate_rejected", "arm": "right",
                                            "message": str(error), **error.details})
        try:
            # Exactly one native tick. Ignore transient termination tensors; RoboLab's freeze is authoritative.
            self._obs, _, _, _, _ = self.env.step(self._make_action(row))
            self._steps += 1
            self.controller.acknowledge(row)
            self._opening_command = float(target.gripper)
            self._record_frame(self._obs)
            state = self._state()
        except Exception as error:
            self._fault = f"{type(error).__name__}: {error}"
            raise
        return StepReceipt(accepted=True, state=state,
            diagnostics={"executed_steps": 1, "applied_joint_positions": row[:7].tolist(),
                         "applied_finger_joint_rad": float(row[7] * NATIVE_ANGLE_SCALE)})

    def step_native(self, command):
        raise RequestRejected("RoboLab does not advertise raw native action vectors")

    def preview_joints(self, commands):
        raise RequestRejected("RoboLab does not advertise multi-command joint previews")

    def capture(self, cameras=None, *, high_resolution=False):
        return self.owner.run(lambda: self._capture(cameras))

    def _capture(self, cameras):
        names = list(self._obs.get("image_obs", {})) if cameras is None else cameras
        if not names or len(set(names)) != len(names) or set(names) - self._obs.get("image_obs", {}).keys():
            raise RequestRejected("Select unique configured RoboLab cameras")
        frames = {}
        for name in names:
            pixels = array(self._obs["image_obs"][name])[0]
            if pixels.ndim != 3 or pixels.shape[-1] not in (3, 4):
                raise ValueError("Expected native batched RGB/RGBA")
            frame = Image.fromarray(pixels[:, :, :3].astype(np.uint8))
            original = frame.size
            frame.thumbnail((1280, 1280))
            intrinsic, extrinsic = self._camera_state(name, original, frame.size)
            frames[name] = CameraFrame(np.asarray(frame), None, intrinsic, extrinsic)
        state = self._state()
        return CapturedObservation(self.episode_id, state.state_version, time.time(),
                                   state.model_dump(mode="json"), frames, terminated=state.ended).freeze()

    def finish(self, reason, status=None):
        return self.owner.run(lambda: self._finish(reason, status))

    def _finish(self, reason, status):
        if self._result is not None:
            return dict(self._result)
        if self._finalization_started:
            raise RuntimeError("Native finalization failed previously; do not replay it")
        self._finalization_started = self._finished = True
        results = self.env.get_env_results()
        if len(results) != 1 or results[0].get("env_id") != 0:
            raise RuntimeError("Expected one native RoboLab result for env_id=0")
        native = dict(results[0])
        success = native["success"]
        if success is not None and not isinstance(success, (bool, np.bool_)):
            raise RuntimeError("Invalid native RoboLab success value")
        native["success"] = None if success is None else bool(success)
        result = {"task_success": bool(success) if success is not None else False,
            "native_result": native, "native_steps": self._steps,
            "reason": reason, "claimed_status": status,
            "failure_category": "environment_error" if self._fault else "episode_incomplete" if success is None else None,
            "environment_error": self._fault, "metadata": self.metadata}
        # Flush exactly once before publishing the authoritative result.
        self._end_episode(self.env)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / "native_outcome.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(result, indent=2))
        temporary.replace(path)
        self._result = result
        return dict(result)

    def close(self):
        return self.owner.run(self._close)

    def _close(self):
        if not self._closed:
            self._closed = True
            self.env.close()
