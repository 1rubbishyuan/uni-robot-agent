"""Version-specific Arena creation, sensor access and native controller packing."""
import hashlib
import importlib.metadata
from pathlib import Path
import subprocess
import sys

import numpy as np

from ..g1 import CONTROL_DT, CONTROL_GROUPS, JOINT_GROUPS

REVISION = "8fbe412ea1d4468bf5e4b285a092e0eca2a8f1fa"
ISAACLAB_REVISION = "e3f7d9ff1f5d7f8280de3ece9000b7117f66bcf4"
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


def validate_driver(version):
    """CUDA 13 requires the R580 driver family or newer; this is not a GPU test."""
    parts = tuple(int(part) for part in version.strip().split("."))
    if parts < (580, 65, 6):
        raise ValueError(f"This Arena pin requires CUDA 13 / NVIDIA driver >=580.65.06; found {version}. Use a compatible host; do not upgrade a shared driver implicitly.")
    return version


def preflight(path):
    root = validate_source(path)
    if sys.version_info[:2] != (3, 12):
        raise ValueError("The pinned Arena runtime requires Python 3.12")
    version = importlib.metadata.version("isaacsim")
    if version.split(".")[:2] != ["6", "2"]:
        raise ValueError("The pinned Arena runtime requires Isaac Sim 6.2")
    drivers = subprocess.check_output(["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"], text=True).splitlines()
    if not drivers:
        raise ValueError("Arena requires an available NVIDIA GPU driver")
    for driver in drivers:
        validate_driver(driver)
    return {"native_revision": REVISION, "isaaclab_revision": ISAACLAB_REVISION,
            "isaacsim": version, "driver": drivers[0], "native_root": str(root)}


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


def create_native(path, *, seed):
    preflight(path)
    from isaaclab.app import AppLauncher
    app = AppLauncher(headless=True, enable_cameras=True, device="cuda:0").app
    env = None
    try:
        # Kit must be running before importing any Arena USD, scene or task modules.
        from isaaclab_arena_environments.galileo_g1_locomanip_pick_and_place_environment import (
            GalileoG1LocomanipPickAndPlaceEnvironment, GalileoG1LocomanipPickAndPlaceEnvironmentCfg,
        )
        from isaaclab_arena.environments.arena_env_builder import ArenaEnvBuilder
        from isaaclab_arena.environments.arena_env_builder_cfg import ArenaEnvBuilderCfg
        description = GalileoG1LocomanipPickAndPlaceEnvironment().build(
            GalileoG1LocomanipPickAndPlaceEnvironmentCfg(enable_cameras=True, embodiment="g1_wbc_joint"))
        env = ArenaEnvBuilder(description, ArenaEnvBuilderCfg(num_envs=1, seed=seed, presets="physx")).make_registered()
        native = env.unwrapped
        native.configure_episode_limit(1)
        observation, _ = env.reset(seed=seed)
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
