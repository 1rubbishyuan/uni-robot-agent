"""RoboUse lifecycle and primitive actions through the shared environment contract."""
import time
import numpy as np
from .backends import create
from .geometry import CameraCapture, resolve_cameras
from uni_robot_agent.services.environment.contracts import (
    ArmCommand, ArmDescription, CameraDescription, EnvironmentDescription,
    EnvironmentState, PoseCommand, RequestRejected, StepReceipt,
)
from uni_robot_agent.services.environment.snapshots import CapturedObservation


class RoboUseAdapter:
    def __init__(self, episode, *, episode_id, cameras, depth_enabled, snapshot_interval=5):
        self.episode, self.episode_id = episode, episode_id
        kind = episode.spec.get("uni_robot_agent_original_backend", episode.spec["backend"])
        self.binding = create(episode.backend, kind)
        self.commands = {name: 1. for name in self.binding.names}
        measured = self.binding.read(self.commands)
        self.commands = {name: arm.gripper for name, arm in measured.items()}
        if getattr(episode.backend, "uni_robot_agent_worker", False):
            from .backends.worker_control import WorkerCamera
            self.commands = {"arm": episode.backend.control()["drive_opening"]}
            self.camera = WorkerCamera(episode.backend, cameras, depth_enabled)
        else:
            views = resolve_cameras(episode.backend, kind, self.binding.model, cameras)
            self.camera = CameraCapture(self.binding.model, self.binding.data, views, depth_enabled=depth_enabled)
        self.closed = False
        self.description = EnvironmentDescription(
            arms=[ArmDescription(name=n, control_frame=getattr(self.binding, "control_point", "world"), grasp_site_description=(
                "Native measured grasp reference; OSC backends use the midpoint of finger pads."),
                rotation_radius_m=.15, control=getattr(self.binding, "actor_control", None)) for n in self.binding.names],
            cameras=[CameraDescription(name=n, has_depth=depth_enabled) for n in cameras],
            control_frequency_hz=float(self.binding.hz), max_episode_steps=episode.max_steps,
            observation_interval_steps=snapshot_interval,
            action_binding="robouse_cartesian" if getattr(self.binding, "actor_control", None) else None,
            metadata={"backend": kind, "control_constraints": self.binding.constraint,
                      "observation_mode": "rgb_and_robot_proprioception", "depth_enabled": depth_enabled,
                      "yaw_enabled": bool(getattr(episode.backend, "yaw_enabled", False)),
                      "initial_orientation": {n: list(a.control_pose.quaternion) for n, a in measured.items()}},
        )

        from uni_robot_agent.integrations.robot_resources import add_robot_resources
        from .robot_reference import direct_model
        model = (episode.backend.robot_model() if getattr(episode.backend, "uni_robot_agent_worker", False)
                 else direct_model(self.binding))
        add_robot_resources(self.description, model=model)

    def read_state(self):
        ep = self.episode
        return EnvironmentState(arms=self.binding.read(self.commands), state_version=ep.steps,
                                native_steps=ep.steps, ended=ep.finished or ep.steps >= ep.max_steps)

    def validate_commands(self, commands, mode):
        for command in commands:
            if not isinstance(command, PoseCommand) or command.solver != "native":
                raise RequestRejected("RoboUse accepts native-solver pose commands only")
            if set(command.arms) != set(self.commands) or not all(isinstance(v, ArmCommand) for v in command.arms.values()):
                raise RequestRejected("Supply one pose and aperture for every registered arm")
            self.binding.encode(command.arms)

    def step_pose(self, command):
        self.validate_commands([command], "pose")
        if self.read_state().ended:
            raise RequestRejected("Episode has ended")
        action = self.binding.encode(command.arms)
        if len(action) != self.episode.backend.action_spec.dim or not np.isfinite(action).all():
            raise RequestRejected("Invalid native action layout")
        # Episode owns budgets, first-success termination and official grading.
        reply = self.episode.act(action, repeat=1)
        if not reply.get("ok"):
            raise RuntimeError(reply.get("error", "Native action failed"))
        if not self.episode.finished and self.episode.steps >= self.episode.max_steps:
            self.episode.finish("budget_exhausted", "")
        self.commands = {n: float(v.gripper) for n, v in command.arms.items()}
        return StepReceipt(accepted=True, state=self.read_state(),
                           diagnostics={"executed_steps": reply["result"]["executed_steps"]})

    def step_joints(self, command):
        raise RequestRejected("Joint control is not exposed by this RoboUse binding")

    def step_native(self, command):
        raise RequestRejected("Raw actions are not exposed by this RoboUse binding")

    def preview_joints(self, commands):
        raise RequestRejected("Joint preview is unavailable")

    def capture(self, cameras=None, *, high_resolution=False):
        names = list(self.camera.cameras) if cameras is None else cameras
        if not names or len(set(names)) != len(names) or set(names) - self.camera.cameras.keys():
            raise RequestRejected("Select unique configured cameras")
        frames = self.camera.capture(names)
        state = self.read_state()
        return CapturedObservation(self.episode_id, state.state_version, time.time(),
                                   state.model_dump(mode="json"), frames, terminated=state.ended)

    def finish(self, reason, status=None):
        ep = self.episode
        if not ep.finished:
            outcome = "done" if status == "success" else "gave_up" if status == "give_up" else "agent_exited"
            ep.finish(outcome, "")
        result = ep.result
        return {"task_success": bool(result["success"]), "native_steps": ep.steps,
                "reason": reason, "native_outcome": result["outcome"],
                "success_mode": ep.success_mode, "native_result_path": str(ep.run_dir / "result.json")}

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                self.camera.close()
            finally:
                self.episode.close()
