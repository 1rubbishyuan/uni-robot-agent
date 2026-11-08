"""Expose measured DexJoCo device state and one coordinated native control step."""
import time

import numpy as np

from uni_robot_agent.services.environment.contracts import (
    CameraDescription,
    Pose,
    RequestRejected,
)
from uni_robot_agent.services.environment.device_contracts import (
    CoordinatedDeviceStep,
    DeviceDescription,
    DeviceEnvironmentDescription,
    DeviceState,
)
from uni_robot_agent.services.environment.snapshots import CapturedObservation

from .native_compat import (
    CONTROL_DT,
    HAND_JOINTS,
    INSTRUCTION,
    MAX_STEPS,
    REVISION,
    TASK,
    camera_frames,
    hand_limits,
    simulation_time,
)


class DexJoCoAdapter:
    def __init__(self, env, observation, *, episode_id, seed, reset_sha256):
        self.env, self.episode_id = env, episode_id
        self.steps, self.finished, self.closed = 0, False, False
        self.success, self.native_ended, self.outcome = False, False, None
        limits, self.control_limits = hand_limits(env)
        self.metadata = {
            "benchmark": "dexjoco", "task_id": TASK, "native_revision": REVISION,
            "seed": seed, "reset_sha256": reset_sha256, "evaluation_regime": "rand_obj",
            "randomize_dynamics": False, "max_steps": MAX_STEPS,
        }
        self.description = DeviceEnvironmentDescription(
            world_frame="world",
            devices=[
                DeviceDescription(name="right_arm", kind="arm", control_frame="attachment_site",
                    capabilities=["coordinated_step"],
                    metadata={"controller": "native_osc", "quaternion_order": "wxyz"}),
                DeviceDescription(name="right_hand", kind="hand", joint_names=list(HAND_JOINTS),
                    joint_limits=limits, control_frame="right_hand", capabilities=["coordinated_step"],
                    metadata={"controller": "native_position", "target_limit_policy": "clip",
                              "actuator_target_limits": self.control_limits}),
                DeviceDescription(name="robot", kind="assembly", capabilities=["coordinated_step"],
                    metadata={"members": ["right_arm", "right_hand"]}),
            ],
            cameras=[CameraDescription(name=name, has_depth=False, has_calibration=True)
                     for name in ("front", "wrist")],
            metadata={
                "layout": [{"group": "right", "arm": "right_arm", "hand": "right_hand"}],
                "control_frequency_hz": 1 / CONTROL_DT, "native_step_duration_s": CONTROL_DT,
                "max_episode_steps": MAX_STEPS, "action_binding": "dexjoco_tcp_hand",
                "control_constraints": "One absolute world TCP pose and all sixteen hand targets advance together.",
            },
        )
        self._cache(observation)

    def episode_identity(self):
        return INSTRUCTION, dict(self.metadata)

    def _cache(self, observation):
        # The native flattened tail contains object poses and table height. Never retain it.
        values = np.asarray(observation["state"], dtype=float)[:23].copy()
        if values.shape != (23,) or not np.isfinite(values).all():
            raise ValueError("Expected measured TCP pose and sixteen finite hand joints")
        pose = Pose(position=values[:3].tolist(), quaternion=values[3:7].tolist())
        self.measured = {
            "right_arm": {"control_pose": pose.model_dump(mode="json")},
            "right_hand": {"joint_positions_rad": dict(zip(HAND_JOINTS, values[7:].tolist(), strict=True))},
        }
        self.frames = camera_frames(self.env, observation)
        self.measured_at = time.time()

    def read_state(self):
        return DeviceState(devices=self.measured, state_version=self.steps,
            completed_commands=self.steps, measured_at=self.measured_at,
            ended=self.finished or self.native_ended or self.steps >= MAX_STEPS)

    def validate_device(self, command):
        if self.closed or self.read_state().ended:
            raise RequestRejected("DexJoCo episode has ended")
        if (not isinstance(command, CoordinatedDeviceStep) or command.device != "robot"
                or set(command.poses) != {"right_arm"} or set(command.joints) != {"right_hand"}):
            raise RequestRejected("Supply one coordinated robot step with right_arm and right_hand")
        joints = command.joints["right_hand"]
        if set(joints) != set(HAND_JOINTS):
            raise RequestRejected("Supply all sixteen named Allegro joints exactly once")
        for name, (low, high) in zip(HAND_JOINTS, self.control_limits, strict=True):
            if not low <= joints[name] <= high:
                raise RequestRejected(f"Target for {name} exceeds the published native actuator range")

    def preview_device(self, command):
        self.validate_device(command)
        return {"validated": True, "duration_s": CONTROL_DT,
                "commands": command.model_dump(mode="json")}

    def execute_device(self, command, *, cancellation):
        self.validate_device(command)
        if cancellation.is_set():
            return {"execution_status": "rejected", "steps_executed": 0, "duration_s": 0,
                    "stop_reason": "cancelled"}
        pose, joints = command.poses["right_arm"], command.joints["right_hand"]
        action = np.asarray([*pose.position, *pose.quaternion,
                             *(joints[name] for name in HAND_JOINTS)], dtype=np.float64)
        before = simulation_time(self.env)
        # The native step writes OSC torques and hand position controls in the same substeps.
        observation, _, terminated, truncated, info = self.env.step(action)
        self.steps += 1
        self.success = bool(info["succeed"])
        self.native_ended = bool(terminated or truncated)
        self._cache(observation)
        duration = simulation_time(self.env) - before
        if not np.isclose(duration, CONTROL_DT, rtol=0, atol=1e-9):
            raise RuntimeError("Native DexJoCo step duration changed")
        feedback = {"execution_status": "completed", "steps_executed": 1, "duration_s": duration}
        # Cancellation during an atomic step preserves its ACK and stops the caller's next sample.
        if cancellation.is_set():
            feedback["stop_reason"] = "cancelled"
        return feedback

    def interrupt_device(self, device):
        # A synchronous native step is atomic; cancellation is checked before the next step.
        if device != "robot":
            raise RequestRejected("Only the coordinated robot can be interrupted")
        return {"accepted": True, "stops_after_current_step": True}

    def capture(self, cameras=None, *, high_resolution=False):
        names = list(self.frames) if cameras is None else cameras
        if not names or len(set(names)) != len(names) or set(names) - set(self.frames):
            raise RequestRejected("Select unique configured DexJoCo cameras")
        state = self.read_state()
        return CapturedObservation(self.episode_id, self.steps, self.measured_at,
            state.model_dump(mode="json"), {name: self.frames[name] for name in names},
            terminated=state.ended)

    def finish(self, reason, status=None):
        if self.outcome is None:
            self.finished = True
            self.outcome = {
                "task_success": self.success, "reason": reason, "native_steps": self.steps,
                "benchmark": "dexjoco", "task_id": TASK, "evaluation_regime": "rand_obj",
                "seed": self.metadata["seed"], "reset_sha256": self.metadata["reset_sha256"],
            }
        return dict(self.outcome)

    def close(self):
        if not self.closed:
            self.closed = True
            self.env.close()
