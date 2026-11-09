"""Version-specific Arena creation, sensor access and native controller packing."""
import hashlib
import importlib.metadata
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import traceback

import numpy as np

from ..g1 import CONTROL_DT, CONTROL_GROUPS, JOINT_GROUPS

REVISION = "aad4f25f44cc439c363c2b60f1fd170dfd10d2a5"
ISAACLAB_REVISION = "5528d986d8909825a29f3c97656108abf054a261"
TASK = "galileo_g1_locomanip_pick_and_place"
INSTRUCTION = "Pick up the brown box from the shelf, and place it into the blue bin on the table located at the right of the shelf."
MAX_STEPS = 1500


def validate_source(path):
    from uni_robot_agent.integrations.simulation import source_root
    root = source_root(Path(path), REVISION)
    head = subprocess.check_output(["git", "-C", str(root / "submodules/IsaacLab"), "rev-parse", "HEAD"], text=True).strip()
    if head != ISAACLAB_REVISION:
        raise ValueError("Arena IsaacLab submodule differs from the pinned revision")
    return root


def preflight(path):
    root = validate_source(path)
    if sys.version_info[:2] != (3, 11):
        raise ValueError("The pinned Arena runtime requires Python 3.11")
    version = importlib.metadata.version("isaacsim")
    if version.split(".")[:2] != ["5", "1"]:
        raise ValueError("The pinned Arena runtime requires Isaac Sim 5.1")
    sources = {}
    for package, expected in {
            "isaaclab": root / "submodules/IsaacLab/source/isaaclab/isaaclab",
            "isaaclab_arena": root / "isaaclab_arena"}.items():
        spec = importlib.util.find_spec(package)
        actual = Path(spec.origin).resolve() if spec and spec.origin else None
        if actual is None or actual.parent != expected.resolve():
            raise ValueError(f"{package} must import from the pinned Arena checkout; found {actual}")
        sources[package] = str(actual)
    drivers = subprocess.check_output(["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"], text=True).splitlines()
    if not drivers:
        raise ValueError("Arena requires an available NVIDIA GPU driver")
    # Metadata and driver discovery cannot establish Kit/RTX compatibility.
    return {"native_revision": REVISION, "isaaclab_revision": ISAACLAB_REVISION,
            "isaacsim": version, "driver": drivers[0], "native_root": str(root),
            "source_paths": sources, "physics_backend": "PhysX", "physics_device": "cpu"}


class PreserveTerminalState:
    """Keep the terminal state when IsaacLab's step attempts an automatic reset."""
    initial_reset_complete = False

    def _reset_idx(self, env_ids):
        if not self.initial_reset_complete:
            super()._reset_idx(env_ids)


def as_numpy(value):
    if hasattr(value, "detach"):
        return value.detach().cpu().numpy()
    if hasattr(value, "numpy"):
        return value.numpy()
    return np.asarray(value)


def reset_fingerprint(env):
    digest = hashlib.sha256()
    def visit(value):
        if isinstance(value, dict):
            for key in sorted(value):
                digest.update(str(key).encode() + b"\0")
                visit(value[key])
        else:
            array = np.asarray(as_numpy(value), dtype="<f8")
            digest.update(str(array.shape).encode() + b"\0" + array.tobytes())
    visit(env.scene.get_state(is_relative=True))
    return digest.hexdigest()



def refresh_initial_observation(env):
    """Flush the pinned RTX pipeline without advancing or changing the reset state."""
    native = env.unwrapped
    before = reset_fingerprint(native)
    counters = (native.common_step_counter, native._sim_step_counter)
    # Isaac Sim 5.1 can return the reset camera before the scene render is ready.
    for _ in range(4):
        native.sim.render()
    native.scene.sensors["robot_head_cam"].update(0., force_recompute=True)
    observation = native.observation_manager.compute()
    if ((native.common_step_counter, native._sim_step_counter) != counters
            or reset_fingerprint(native) != before):
        raise RuntimeError("Initial camera refresh changed the native reset state")
    return observation


def create_native(path, *, seed, assets_dir=None):
    preflight(path)
    from isaaclab.app import AppLauncher
    kit_args = ["--allow-root"] if os.geteuid() == 0 else []
    if assets_dir is not None:
        assets = Path(assets_dir).expanduser().resolve(strict=True)
        if not (assets / "Isaac").is_dir() or any(character.isspace() for character in str(assets)):
            raise ValueError("Arena assets directory must contain Isaac and have no whitespace")
        kit_args.append(f"--/persistent/isaac/asset_root/cloud={assets}")
    app = AppLauncher(headless=True, enable_cameras=True, device="cpu", enable_pinocchio=True,
                      kit_args=" ".join(kit_args)).app
    env = None
    try:
        # Kit must be running before importing any Arena USD, scene or task modules.
        from isaaclab.envs import ManagerBasedRLEnv
        from isaaclab_arena.cli.isaaclab_arena_cli import get_isaaclab_arena_cli_parser
        from isaaclab_arena_environments.galileo_g1_locomanip_pick_and_place_environment import (
            GalileoG1LocomanipPickAndPlaceEnvironment,
        )
        from isaaclab_arena.environments.arena_env_builder import ArenaEnvBuilder

        class TerminalPreservingEnv(PreserveTerminalState, ManagerBasedRLEnv):
            pass

        parser = get_isaaclab_arena_cli_parser()
        GalileoG1LocomanipPickAndPlaceEnvironment.add_cli_args(parser)
        args = parser.parse_args(["--headless", "--enable_cameras", "--device", "cpu",
            "--embodiment", "g1_wbc_joint", "--seed", str(seed), "--placement_seed", str(seed)])
        description = GalileoG1LocomanipPickAndPlaceEnvironment().get_env(args)
        _, config = ArenaEnvBuilder(description, args).build_registered()
        config.seed = seed
        env = TerminalPreservingEnv(cfg=config)
        native = env.unwrapped
        env.reset(seed=seed)
        native.initial_reset_complete = True
        observation = refresh_initial_observation(env)
        action = native.action_manager.get_term("g1_action")
        if (not np.isclose(native.step_dt, CONTROL_DT) or native.cfg.decimation != 4
                or action.action_dim != 50 or action.get_wbc_version != "homie_v2"):
            raise ValueError("Pinned Arena timing or whole-body action contract changed")
        if set(native.scene["robot"].data.joint_names) != {
                *[name for names in JOINT_GROUPS.values() for name in names],
                *[side + "_" + joint + "_joint" for side in ("left", "right") for joint in
                  ("hip_pitch", "hip_roll", "hip_yaw", "knee", "ankle_pitch", "ankle_roll")]}:
            raise ValueError("Pinned Arena G1 joint names changed")
        return env, app, observation, reset_fingerprint(native)
    except BaseException:
        # Kit close may terminate Python; persist the native failure before cleanup.
        traceback.print_exc()
        if env is not None:
            env.close()
        app.close()
        raise


def joint_limits(env):
    robot = env.unwrapped.scene["robot"]
    ranges = as_numpy(robot.data.soft_joint_pos_limits)[0]
    return dict(zip(robot.data.joint_names, ranges.tolist(), strict=True))


def measured_joints(env, observation):
    names = env.unwrapped.scene["robot"].data.joint_names
    positions = as_numpy(observation["policy"]["robot_joint_pos"])[0]
    return dict(zip(names, positions.astype(float).tolist(), strict=True))


def head_rgb(observation):
    rgb = as_numpy(observation["camera_obs"]["robot_head_cam_rgb"])[0, :, :, :3]
    if rgb.dtype != np.uint8 or rgb.shape != (480, 640, 3):
        raise ValueError("Arena head RGB differs from the pinned 480x640 camera contract")
    return rgb.copy()


def action_tensor(env, command):
    import torch
    names = env.unwrapped.scene["robot"].data.joint_names
    # Homie owns leg and waist targets; their native wire slots remain placeholders.
    joints = {name: target.position for group in CONTROL_GROUPS for name, target in command.joints[group].items()}
    velocity = command.velocities["base"]
    values = [joints.get(name, 0.) for name in names]
    values += [*velocity.linear_m_s[:2], velocity.angular_rad_s[2], command.heights["body"].height_m, 0., 0., 0.]
    return torch.tensor([values], dtype=torch.float32, device=env.unwrapped.device)


def task_success(env):
    return bool(as_numpy(env.unwrapped.termination_manager.get_term("success"))[0])
