"""Access version-specific DexJoCo internals only at the native boundary."""
import hashlib
import random
import sys
from pathlib import Path

REVISION = "8d23b0fab23b17a58c4b55f3942e17013aaf8267"
TASK = "pick_bucket"
HAND_JOINTS = tuple(f"{finger}j{index}" for finger in ("ff", "mf", "rf", "th") for index in range(4))
INSTRUCTION = "Place the boxed food into the bucket and then lift the bucket."
CONTROL_DT = .02
MAX_STEPS = 1200


def validate_source(path):
    from uni_robot_agent.integrations.simulation import source_root
    root = source_root(Path(path), REVISION)
    sys.path.insert(0, str(root / "dexjoco"))
    return root


def create_native(path, *, task=TASK, seed=0):
    if task != TASK:
        raise ValueError("DexJoCo currently admits pick_bucket only")
    if seed < 0:
        raise ValueError("DexJoCo seed must be nonnegative")
    root = validate_source(path)
    import numpy as np
    from dexjoco.tasks.pick_bucket import config

    if not Path(config.__file__).resolve().is_relative_to(root):
        raise ValueError("Imported DexJoCo differs from the pinned source")
    # Native reset(seed=...) does not reseed global RNGs. Seed before construction.
    random.seed(seed)
    np.random.seed(seed)
    env = config.TaskConfig().get_environment(
        policy_mode=True, render_mode="rgb_array", randomize=False,
        randomize_dynamics=False, seed=seed,
    )
    try:
        observation, _ = env.reset()
        native = env.unwrapped
        if (tuple(native._allegro_joint_names) != HAND_JOINTS
                or not np.isclose(native.control_dt, CONTROL_DT)
                or not np.isclose(native._n_substeps * native.physics_dt, CONTROL_DT)):
            raise ValueError("Native DexJoCo control contract changed")
        if (native._allegro_ctrl_ids < 0).any():
            raise ValueError("All sixteen Allegro position actuators are required")
        return env, observation, reset_fingerprint(native)
    except BaseException:
        env.close()
        raise


def reset_fingerprint(native):
    """An opaque reproducibility identity; raw scene state stays private."""
    import numpy as np
    digest = hashlib.sha256()
    for values in (native.data.qpos, native.model.body_pos, native.model.cam_pos,
                   native.model.cam_quat, native.model.geom_size):
        value = np.asarray(values, dtype="<f8")
        digest.update(str(value.shape).encode() + b"\0" + value.tobytes())
    return digest.hexdigest()


def hand_limits(env):
    native = env.unwrapped
    limits = [tuple(map(float, native.model.joint(name).range)) for name in HAND_JOINTS]
    ids = native._allegro_ctrl_ids
    if not native.model.actuator_ctrllimited[ids].all():
        raise ValueError("Expected native bounded Allegro position actuators")
    controls = [tuple(map(float, pair)) for pair in native.model.actuator_ctrlrange[ids]]
    return limits, controls


def simulation_time(env):
    return float(env.unwrapped.data.time)


def camera_frames(env, observation):
    import numpy as np

    from uni_robot_agent.services.environment.snapshots import CameraFrame
    native = env.unwrapped
    frames = {}
    for name, camera_id in (("front", native._front_camera_id), ("wrist", native._wrist_camera_id)):
        rgb = np.asarray(observation[name]).copy()
        height, width = rgb.shape[:2]
        focal = .5 * height / np.tan(np.deg2rad(native.model.cam_fovy[camera_id]) / 2)
        intrinsic = np.array([[focal, 0, width / 2], [0, focal, height / 2], [0, 0, 1]])
        transform = np.eye(4)
        # MuJoCo camera axes are +X right, +Y up, -Z forward; public optical axes are +Y down/+Z forward.
        transform[:3, :3] = native.data.cam_xmat[camera_id].reshape(3, 3) @ np.diag([1., -1., -1.])
        transform[:3, 3] = native.data.cam_xpos[camera_id]
        frames[name] = CameraFrame(rgb, intrinsic_K=intrinsic, T_world_from_optical=transform)
    return frames
