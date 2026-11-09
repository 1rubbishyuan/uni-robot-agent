"""Publish G1 proprioception and execute one native coordinated WBC sample."""
import time

import numpy as np

from uni_robot_agent.services.environment.contracts import CameraDescription, RequestRejected
from uni_robot_agent.services.environment.device_contracts import (
    CoordinatedControlStep, DeviceDescription, DeviceEnvironmentDescription, DeviceState,
)
from uni_robot_agent.services.environment.snapshots import CameraFrame, CapturedObservation

from ..g1 import CONTROL_DT, CONTROL_GROUPS, JOINT_GROUPS
from . import native_compat as native


class ArenaAdapter:
    def __init__(self, env, app, observation, *, episode_id, seed, reset_sha256):
        self.env, self.app, self.episode_id = env, app, episode_id
        self.steps = 0
        self.finished = self.closed = self.native_ended = self.success = False
        self.outcome = None
        self.limits = native.joint_limits(env)
        self.metadata = {"benchmark": "isaaclab_arena", "task_id": native.TASK, "seed": seed,
            "native_revision": native.REVISION, "isaaclab_revision": native.ISAACLAB_REVISION,
            "reset_sha256": reset_sha256, "max_steps": native.MAX_STEPS,
            "benchmark_release": "arena_v0.2_on_lab_2.3", "physics_backend": "PhysX",
            "physics_device": "cpu", "native_success_predicate": "objects_in_proximity",
            "success_thresholds_m": {"x": .260, "y": .130, "z": .150}}
        devices = [DeviceDescription(name=group, kind="body" if group == "waist" else
                    "hand" if group.endswith("hand") else "arm", joint_names=names,
                    joint_limits=[tuple(self.limits[name]) for name in names],
                    joint_units={name: "rad" for name in names},
                    capabilities=[] if group == "waist" else ["coordinated_control"],
                    metadata={"controller": "native_homie" if group == "waist" else "native_position"})
                   for group, names in JOINT_GROUPS.items()]
        devices += [DeviceDescription(name="base", kind="base", control_frame="base_heading",
                        capabilities=["coordinated_control"], metadata={"controller": "native_homie"}),
                    DeviceDescription(name="body", kind="body", control_frame="ground",
                        capabilities=["coordinated_control"], metadata={"reference": "pelvis_height", "controller": "native_homie"}),
                    DeviceDescription(name="robot", kind="assembly", capabilities=["coordinated_control"],
                        metadata={"members": [*JOINT_GROUPS, "base", "body"]})]
        self.description = DeviceEnvironmentDescription(world_frame="world", devices=devices,
            cameras=[CameraDescription(name="head", has_depth=False, has_calibration=False)],
            metadata={"embodiment": "g1_wbc_joint", "wbc": "homie_v2", "control_frequency_hz": 50,
                "native_step_duration_s": CONTROL_DT, "max_episode_steps": native.MAX_STEPS,
                "action_binding": "arena_g1_locomanipulation", "simulation_clock": "synchronous",
                "native_controlled_joint_groups": ["legs", "waist"]})
        self._cache(observation)

    def episode_identity(self):
        return native.INSTRUCTION, dict(self.metadata)

    def _cache(self, observation):
        joints = native.measured_joints(self.env, observation)
        self.measured = {group: {"joint_positions_rad": {name: joints[name] for name in names}}
                         for group, names in JOINT_GROUPS.items()}
        if not all(np.isfinite(value) for group in self.measured.values() for value in group["joint_positions_rad"].values()):
            raise ValueError("G1 measured joints must be finite")
        self.frames = {"head": CameraFrame(native.head_rgb(observation))}
        self.measured_at = time.time()

    def read_state(self):
        return DeviceState(devices=self.measured, state_version=self.steps, completed_commands=self.steps,
            measured_at=self.measured_at, ended=self.finished or self.native_ended,
            metadata={"simulation_time_s": self.steps * CONTROL_DT})

    def validate_device(self, command):
        if self.closed or self.read_state().ended:
            raise RequestRejected("Arena episode has ended")
        if (not isinstance(command, CoordinatedControlStep) or command.device != "robot"
                or command.duration_s != CONTROL_DT or set(command.joints) != set(CONTROL_GROUPS)
                or set(command.velocities) != {"base"} or set(command.heights) != {"body"}):
            raise RequestRejected("Supply one complete coordinated 50 Hz G1 sample")
        velocity = command.velocities["base"]
        if (velocity.frame != "base_heading" or velocity.linear_m_s[2] != 0.
                or velocity.angular_rad_s[:2] != (0., 0.) or command.heights["body"].frame != "ground"):
            raise RequestRejected("G1 requires planar base-heading velocity and a ground-relative height reference")
        for group, targets in command.joints.items():
            if set(targets) != set(JOINT_GROUPS[group]):
                raise RequestRejected("G1 samples must address every declared arm and hand joint")
            for name, target in targets.items():
                if target.unit != "rad":
                    raise RequestRejected("G1 joint targets must be in radians")
                low, high = self.limits[name]
                if not low <= target.position <= high:
                    raise RequestRejected(f"G1 target {name} exceeds its declared native joint limits")

    def preview_device(self, command):
        self.validate_device(command)
        return {"validated": True, "duration_s": CONTROL_DT, "commands": command.model_dump(mode="json")}

    def execute_device(self, command, *, cancellation):
        self.validate_device(command)
        if cancellation.is_set():
            return {"execution_status": "rejected", "steps_executed": 0, "duration_s": 0., "stop_reason": "cancelled"}
        before = self.env.unwrapped.common_step_counter
        observation, _, terminated, truncated, _ = self.env.step(native.action_tensor(self.env, command))
        self.steps += 1
        self.native_ended = bool(native.as_numpy(terminated)[0] or native.as_numpy(truncated)[0])
        self.success = native.task_success(self.env)
        self._cache(observation)
        if self.env.unwrapped.common_step_counter != before + 1:
            raise RuntimeError("Arena did not acknowledge exactly one native control step")
        if self.steps >= native.MAX_STEPS and not self.native_ended:
            raise RuntimeError("Arena native episode exceeded its configured thirty-second budget")
        feedback = {"execution_status": "completed", "steps_executed": 1, "duration_s": CONTROL_DT}
        if cancellation.is_set():
            feedback["stop_reason"] = "cancelled"
        return feedback

    def interrupt_device(self, device):
        if device != "robot":
            raise RequestRejected("Only the coordinated G1 assembly can be interrupted")
        return {"accepted": True, "stops_after_current_step": True}

    def capture(self, cameras=None, *, high_resolution=False):
        if cameras is not None and cameras != ["head"]:
            raise RequestRejected("The admitted Arena profile exposes the head camera")
        state = self.read_state()
        return CapturedObservation(self.episode_id, self.steps, self.measured_at,
            state.model_dump(mode="json"), self.frames, terminated=state.ended)

    def finish(self, reason, status=None):
        if self.outcome is None:
            self.finished = True
            self.outcome = {"task_success": self.success, "reason": reason, "native_steps": self.steps,
                            **self.metadata}
        return dict(self.outcome)

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                self.env.close()
            finally:
                self.app.close()
