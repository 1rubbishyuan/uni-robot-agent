"""Pinned LIBERO-family native observations, OSC actions and task outcomes."""
import time
from types import SimpleNamespace

import numpy as np

from uni_robot_agent.integrations.robouse.environment.backends.osc import OSC
from uni_robot_agent.services.environment.contracts import (
    ArmCommand, ArmDescription, CameraDescription, EnvironmentDescription,
    EnvironmentState, PoseCommand, RequestRejected, StepReceipt,
)
from uni_robot_agent.services.environment.snapshots import CameraFrame, CapturedObservation

CAMERAS = ("agentview", "robot0_eye_in_hand")


def observation_frames(obs, sim, names, *, depth_enabled, convention, distorted_agentview=False):
    """Keep upstream corruptions; never replace an observation with a clean render."""
    from robosuite.utils.camera_utils import get_camera_intrinsic_matrix, get_camera_extrinsic_matrix, get_real_depth_map

    frames = {}
    for name in names:
        # robosuite's OpenGL images are bottom-up; OpenCV images are already upright.
        stride = -1 if convention == "opengl" else 1
        rgb = np.asarray(obs[name + "_image"])[::stride].copy()
        height, width = rgb.shape[:2]
        intrinsic = get_camera_intrinsic_matrix(sim, name, height, width)
        transform = get_camera_extrinsic_matrix(sim, name)
        distorted = name == "agentview" and distorted_agentview
        if distorted:
            intrinsic = transform = None
        depth = None
        if depth_enabled and not distorted:
            depth = get_real_depth_map(sim, np.asarray(obs[name + "_depth"]).reshape(height, width))[::stride].copy()
        frames[name] = CameraFrame(rgb, depth, intrinsic, transform)
    return frames


class LiberoAdapter:
    def __init__(self, env, obs, *, episode_id, instruction, metadata, max_steps, depth_enabled, convention):
        self.env, self.obs = env, obs
        self.episode_id, self.instruction, self.metadata = episode_id, instruction, metadata
        self.max_steps, self.depth_enabled, self.convention = max_steps, depth_enabled, convention
        noise = getattr(env, "noise", 0)
        # Zoom/glass corruptions move pixels without changing the native camera.
        self.distorted_agentview = 20 < noise <= 30 or noise > 40
        native = SimpleNamespace(env=env, action_spec=SimpleNamespace(dim=7),
                                 mj_model_data=lambda: (env.sim.model._model, env.sim.data._data))
        self.binding = OSC(native, "libero")
        self.commands = {"arm": 1.0}
        self.steps, self.success, self.ended, self.closed = 0, False, False, False
        self.outcome = None
        self.description = EnvironmentDescription(
            arms=[ArmDescription(name="arm", control_frame="world", rotation_radius_m=.15,
                                 grasp_site_description="Midpoint of the Panda finger pads; opening is normalized 0 closed, 1 open.")],
            cameras=[CameraDescription(name=name,
                has_depth=depth_enabled and not (name == "agentview" and self.distorted_agentview),
                has_calibration=not (name == "agentview" and self.distorted_agentview)) for name in CAMERAS],
            control_frequency_hz=20.0, max_episode_steps=max_steps,
            metadata={"control_constraints": self.binding.constraint, "observation_source": "native_libero_wrapper"},
        )

        from uni_robot_agent.integrations.robot_resources import add_robot_resources, mujoco_robot_model
        model = self.binding.model
        add_robot_resources(self.description, model=mujoco_robot_model(
            model, [model.site_bodyid[entry[1]] for entry in self.binding.entries]))

    def episode_identity(self):
        return self.instruction, self.metadata

    def read_state(self):
        return EnvironmentState(arms=self.binding.read(self.commands), state_version=self.steps,
                                native_steps=self.steps, ended=self.ended)

    def validate_commands(self, commands, mode):
        if mode != "pose":
            raise RequestRejected("Only native-solver pose control is available")
        for command in commands:
            if (not isinstance(command, PoseCommand) or command.solver != "native"
                    or set(command.arms) != {"arm"} or not isinstance(command.arms["arm"], ArmCommand)):
                raise RequestRejected("Supply one native-solver pose and opening for arm")
            self.binding.encode(command.arms)

    def step_pose(self, command):
        self.validate_commands([command], "pose")
        if self.ended or self.closed:
            raise RequestRejected("Episode has ended")
        self.obs, _, done, _ = self.env.step(self.binding.encode(command.arms))
        self.steps += 1
        self.commands = {"arm": float(command.arms["arm"].gripper)}
        self.success = bool(self.env.check_success())
        self.ended = bool(done or self.success or self.steps >= self.max_steps)
        return StepReceipt(accepted=True, state=self.read_state(), diagnostics={"executed_steps": 1})

    def step_joints(self, command):
        raise RequestRejected("Joint control is unavailable")

    def step_native(self, command):
        raise RequestRejected("Raw action control is unavailable")

    def preview_joints(self, commands):
        raise RequestRejected("Joint previews are unavailable")

    def capture(self, cameras=None, *, high_resolution=False):
        names = list(CAMERAS) if cameras is None else cameras
        if not names or len(names) != len(set(names)) or set(names) - set(CAMERAS):
            raise RequestRejected("Select unique configured cameras")
        frames = observation_frames(self.obs, self.env.sim, names, depth_enabled=self.depth_enabled,
                                    convention=self.convention, distorted_agentview=self.distorted_agentview)
        state = self.read_state()
        return CapturedObservation(self.episode_id, self.steps, time.time(), state.model_dump(mode="json"),
                                   frames, terminated=self.ended)

    def finish(self, reason, status=None):
        if self.outcome is None:
            self.ended = True
            self.outcome = {"task_success": bool(self.success), "native_steps": self.steps, "reason": reason,
                            "benchmark": self.metadata["benchmark"], "init_state_index": self.metadata["init_state_index"]}
        return dict(self.outcome)

    def close(self):
        if not self.closed:
            self.closed = True
            self.env.close()
