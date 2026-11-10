"""Execute bounded Go2 velocity segments and publish real intermediate RGB."""
import hashlib
import json
import math
import time

from uni_robot_agent.services.environment.contracts import CameraDescription, RequestRejected
from uni_robot_agent.services.environment.device_contracts import (
    CoordinatedControlStep, DeviceDescription, DeviceEnvironmentDescription, DeviceState,
)
from uni_robot_agent.services.environment.snapshots import CameraFrame, CapturedHistoryFrame, CapturedObservation

from .native_compat import CONTROL_DT, MAX_STEPS, REVISION

HISTORY = {"tick_origin": "post_warmup", "period_ticks": 25, "first_capture_tick": 1,
           "include_initial_frame": True, "control_dt_s": CONTROL_DT, "scope": "episode"}


class NaVILAAdapter:
    def __init__(self, native, *, episode_id, task):
        self.native, self.episode_id = native, episode_id
        self.steps, self.completed_commands = 0, 0
        self.native_ended, self.finished, self.closed = False, False, False
        self.outcome = None
        self.history = []
        self._cache()
        self._record_frame()
        digest = hashlib.sha256(self.frame.rgb.tobytes() + json.dumps(self.measured, sort_keys=True).encode()).hexdigest()
        self.metadata = {"benchmark": "navila", "task_id": task, "native_revision": REVISION,
            "runtime": dict(native.runtime), "seed": native.seed, "reset_sha256": digest, "warmup_steps": 100,
            "locomotion_checkpoint_sha256": native.policy_sha256, "max_episode_steps": MAX_STEPS,
            "evaluation_regime": "hybrid_explicit_finish", "native_history": dict(HISTORY)}
        self.description = DeviceEnvironmentDescription(world_frame="world",
            devices=[DeviceDescription(name="base", kind="base", control_frame="body",
                joint_names=list(self.measured["joint_positions_rad"]),
                capabilities=["body_velocity"], metadata={"controller": "go2_vision_rsl_rl",
                    "controller_inputs": "proprioception, command and LiDAR height map",
                    "max_forward_m_s": .5, "max_yaw_rad_s": math.pi / 6})],
            cameras=[CameraDescription(name="front", has_depth=False, has_calibration=False)],
            metadata={"action_binding": "navila_go2", "control_frequency_hz": 1 / CONTROL_DT,
                "camera_history": {"front": dict(HISTORY)}, "max_episode_steps": MAX_STEPS,
                "max_segment_duration_s": 1.5, "native_step_duration_s": CONTROL_DT})

    def episode_identity(self):
        return self.native.instruction, dict(self.metadata)

    def _cache(self, *, capture=True):
        if capture:
            self.frame = CameraFrame(self.native.frame()).freeze()
        self.measured = self.native.measured_state()
        self.measured_at = time.time()

    def _record_frame(self):
        self.history.append(CapturedHistoryFrame(f"{self.episode_id}:front:{self.steps}",
            self.measured_at, self.steps * CONTROL_DT, self.steps, self.frame))

    def read_state(self):
        return DeviceState(devices={"base": self.measured}, state_version=self.steps,
            completed_commands=self.completed_commands, measured_at=self.measured_at,
            ended=self.finished or self.native_ended or self.steps >= MAX_STEPS,
            metadata={"simulation_time_s": self.steps * CONTROL_DT})

    def validate_device(self, command):
        if self.closed or self.read_state().ended:
            raise RequestRejected("The NaVILA episode has ended")
        if (not isinstance(command, CoordinatedControlStep) or command.device != "base"
                or command.joints or command.heights or set(command.velocities) != {"base"}):
            raise RequestRejected("NaVILA admits one body-frame Go2 velocity segment")
        velocity = command.velocities["base"]
        vx, vy, vz = velocity.linear_m_s
        roll, pitch, yaw = velocity.angular_rad_s
        ticks = command.duration_s / CONTROL_DT
        if (velocity.frame != "body" or vy != 0 or vz != 0 or roll != 0 or pitch != 0
                or not 0 <= vx <= .5 or abs(yaw) > math.pi / 6 + 1e-12
                or not 0 < command.duration_s <= 1.5
                or not math.isclose(ticks, round(ticks), abs_tol=1e-9)):
            raise RequestRejected("Go2 segment violates its declared velocity, frame or duration limits")

    def preview_device(self, command):
        self.validate_device(command)
        return {"validated": True, "duration_s": command.duration_s,
                "command": command.model_dump(mode="json")}

    def execute_device(self, command, *, cancellation):
        self.validate_device(command)
        target_steps = round(command.duration_s / CONTROL_DT)
        velocity = command.velocities["base"]
        requested = [velocity.linear_m_s[0], 0., velocity.angular_rad_s[2]]
        start_tick = self.steps
        executed, stop_reason = 0, None
        for _ in range(target_steps):
            if cancellation.is_set():
                stop_reason = "cancelled"
                break
            if self.read_state().ended:
                stop_reason = "native_terminal"
                break
            self.native_ended = self.native.step(requested)
            self.steps += 1
            executed += 1
            capture = ((self.steps - 1) % HISTORY["period_ticks"] == 0
                       or executed == target_steps or self.native_ended or self.steps >= MAX_STEPS)
            self._cache(capture=capture)
            # Match the pinned evaluator's post-step capture phase, including tick one.
            if (self.steps - 1) % HISTORY["period_ticks"] == 0:
                self._record_frame()
            if self.read_state().ended:
                stop_reason = "native_terminal"
                break
        if stop_reason == "cancelled" and executed:
            self._cache()
        completed = executed == target_steps
        self.completed_commands += int(completed)
        return {"execution_status": "completed" if completed else "partial" if executed else "rejected",
                "steps_executed": executed, "samples_completed": int(completed),
                "native_tick_start": start_tick, "native_tick_end": self.steps,
                "duration_s": executed * CONTROL_DT, "stop_reason": stop_reason,
                "requested_forward_m": requested[0] * command.duration_s,
                "requested_yaw_rad": requested[2] * command.duration_s}

    def interrupt_device(self, device):
        if device != "base":
            raise RequestRejected("Only the Go2 base supports interruption")
        return {"accepted": True, "stops_after_current_step": True}

    def capture(self, cameras=None, *, high_resolution=False):
        if cameras is not None and cameras != ["front"]:
            raise RequestRejected("NaVILA exposes one front RGB camera")
        state = self.read_state()
        return CapturedObservation(self.episode_id, self.steps, self.measured_at,
            state.model_dump(mode="json"), {"front": self.frame}, terminated=state.ended,
            truncated=self.steps >= MAX_STEPS, history={"front": tuple(self.history)})

    def finish(self, reason, status=None):
        if self.outcome is None:
            # A planner finish is a new navigation STOP declaration. A fall or exhausted
            # episode budget cannot become a successful STOP by finalizing afterwards.
            stopped = (reason == "completion_claimed" and status == "success"
                       and not self.native_ended and self.steps < MAX_STEPS)
            measurements = self.native.finish(stopped)
            self.finished = True
            self.outcome = {"task_success": bool(measurements["success"]), "reason": reason,
                "navigation_stop_declared": stopped, "measurements": measurements,
                "native_steps": self.steps, **self.metadata}
        return dict(self.outcome)

    def close(self):
        if not self.closed:
            self.closed = True
            self.native.close()
