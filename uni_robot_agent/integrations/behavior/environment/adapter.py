"""Execute one complete R1Pro physical command per native control step."""
import time

import numpy as np

from uni_robot_agent.services.environment.contracts import CameraDescription, RequestRejected
from uni_robot_agent.services.environment.device_contracts import (
    CoordinatedControlStep, DeviceDescription, DeviceEnvironmentDescription, DeviceState,
)
from uni_robot_agent.services.environment.snapshots import CapturedObservation

from .native_compat import CAMERAS, COMPONENTS, CONTROL_DT
from .observations import project_observation


class BehaviorAdapter:
    def __init__(self, native, *, episode_id):
        self.native, self.episode_id = native, episode_id
        self.steps, self.finished, self.native_ended, self.closed = 0, False, False, False
        self.outcome = None
        self.metadata = {"benchmark": "behavior", **native.metadata}
        devices = [DeviceDescription(name="base", kind="base", control_frame="base",
                    capabilities=["coordinated_control"], metadata=native.controllers["base"])]
        for name in COMPONENTS:
            spec = native.controllers[name]
            kind = "hand" if name.endswith("gripper") else "arm" if name.endswith("arm") else "body"
            devices.append(DeviceDescription(name=name, kind=kind, joint_names=spec["joint_names"],
                joint_units=dict.fromkeys(spec["joint_names"], spec["unit"]), joint_limits=spec["joint_limits"],
                capabilities=["coordinated_control"], metadata={key: spec[key] for key in
                    ("input_limits", "output_limits", "inverted") if key in spec}))
        devices.append(DeviceDescription(name="robot", kind="assembly", capabilities=["coordinated_control"],
                        metadata={"members": ["base", *COMPONENTS]}))
        self.devices = {item.name: item for item in devices}
        self.description = DeviceEnvironmentDescription(devices=devices, world_frame="world",
            cameras=[CameraDescription(name=name, has_depth=False, has_calibration=False) for name in CAMERAS],
            metadata={"control_frequency_hz": 30, "native_step_duration_s": CONTROL_DT,
                      "max_episode_steps": native.metadata["max_steps"], "action_binding": "behavior_r1pro_joint",
                      "control_constraints": "Base, trunk, arms and symmetric grippers advance together."})
        self._cache()

    def episode_identity(self):
        return self.native.instruction, dict(self.metadata)

    def _cache(self):
        self.measured, self.frames = project_observation(self.native)
        self.measured_at = time.time()

    def read_state(self):
        return DeviceState(devices=self.measured, state_version=self.steps, completed_commands=self.steps,
            measured_at=self.measured_at, ended=self.finished or self.native_ended or self.steps >= self.metadata["max_steps"])

    def validate_device(self, command):
        if self.closed or self.read_state().ended:
            raise RequestRejected("BEHAVIOR episode has ended")
        if (not isinstance(command, CoordinatedControlStep) or command.device != "robot" or command.heights
                or set(command.joints) != set(COMPONENTS) or set(command.velocities) != {"base"}
                or not np.isclose(command.duration_s, CONTROL_DT, atol=1e-10, rtol=0)):
            raise RequestRejected("Supply all R1Pro components for exactly one 30 Hz sample")
        velocity = command.velocities["base"]
        if (velocity.frame != "base" or velocity.linear_m_s[2] != 0 or any(velocity.angular_rad_s[:2])
                or any(abs(value) > maximum for value, maximum in zip(
                    [*velocity.linear_m_s[:2], velocity.angular_rad_s[2]], [.75, .75, 1.], strict=True))):
            raise RequestRejected("R1Pro base requires bounded local planar velocity")
        for name, targets in command.joints.items():
            device = self.devices[name]
            if set(targets) != set(device.joint_names):
                raise RequestRejected(f"Supply every named {name} joint")
            for joint, (lo, hi) in zip(device.joint_names, device.joint_limits, strict=True):
                target = targets[joint]
                if target.unit != device.joint_units[joint] or not lo <= target.position <= hi:
                    raise RequestRejected(f"Invalid physical target for {name}.{joint}")
            if name.endswith("gripper") and len({target.position for target in targets.values()}) != 1:
                raise RequestRejected("Native R1Pro gripper control requires symmetric finger targets")

    def native_action(self, command):
        self.validate_device(command)
        velocity = command.velocities["base"]
        action = np.zeros(23, dtype=np.float32)
        action[:3] = [velocity.linear_m_s[0] / .75, velocity.linear_m_s[1] / .75, velocity.angular_rad_s[2]]
        for name, (start, stop) in COMPONENTS.items():
            device = self.devices[name]
            values = [command.joints[name][joint].position for joint in device.joint_names]
            if name.endswith("gripper"):
                lo, hi = (float(item[0]) for item in device.metadata["output_limits"])
                value = 2 * (values[0] - lo) / (hi - lo) - 1
                action[start] = -value if device.metadata["inverted"] else value
            else:
                action[start:stop] = values
        return action

    def preview_device(self, command):
        self.validate_device(command)
        return {"validated": True, "duration_s": CONTROL_DT, "commands": command.model_dump(mode="json")}

    def execute_device(self, command, *, cancellation):
        action = self.native_action(command)
        if cancellation.is_set():
            return {"execution_status": "rejected", "steps_executed": 0, "duration_s": 0., "stop_reason": "cancelled"}
        terminated, truncated, duration = self.native.advance(action)
        self.steps += 1
        self.native_ended = terminated or truncated
        self._cache()
        result = {"execution_status": "completed", "steps_executed": 1, "duration_s": duration}
        # Cancellation during the indivisible native step preserves its acknowledgement.
        if cancellation.is_set():
            result["stop_reason"] = "cancelled"
        return result

    def interrupt_device(self, device):
        if device != "robot":
            raise RequestRejected("Interrupt the coordinated robot assembly")
        return {"accepted": True, "stops_after_current_step": True}

    def capture(self, cameras=None, *, high_resolution=False):
        names = list(CAMERAS) if cameras is None else cameras
        if not names or len(names) != len(set(names)) or set(names) - set(CAMERAS):
            raise RequestRejected("Select unique configured BEHAVIOR cameras")
        state = self.read_state()
        return CapturedObservation(self.episode_id, self.steps, self.measured_at, state.model_dump(mode="json"),
                                   {name: self.frames[name] for name in names}, terminated=state.ended)

    def finish(self, reason, status=None):
        if self.outcome is None:
            self.finished = True
            self.outcome = {**self.native.result(), **self.metadata, "reason": reason, "native_steps": self.steps}
        return dict(self.outcome)

    def close(self):
        if not self.closed:
            self.closed = True
            self.native.close()
