"""Compatibility boundary for RoboLab 0.3.1 / pinned DROID benchmark assets."""
import ast
from importlib.metadata import version
from pathlib import Path
import numpy as np

ROBO_LAB_REVISION = "ad45d4f974725d020f82c2b0d77d78533aeba2b3"


def check_version():
    if version("robolab") != "0.3.1":
        raise ValueError("This adapter requires robolab==0.3.1 and its pinned benchmark assets")


def array(value):
    if hasattr(value, "detach"):
        return value.detach().cpu().numpy()
    # Isaac Lab 2.3 can return Warp arrays, which do not support NumPy indexing directly.
    return value.numpy() if hasattr(value, "numpy") else np.asarray(value)


def benchmark_task_file(task, task_dir):
    """Locate one canonical class without importing unrelated task scenes."""
    root = Path(task_dir) / "benchmark"
    matches = []
    for path in sorted(root.rglob("*.py")):
        if any(part.startswith("_") or part in {"tmp", "not_used"} for part in path.relative_to(root).parts):
            continue
        tree = ast.parse(path.read_text())
        if any(isinstance(node, ast.ClassDef) and node.name == task for node in tree.body):
            matches.append(path)
    if len(matches) != 1:
        raise ValueError(f"Expected one canonical benchmark definition for {task!r}; found {matches}")
    return matches[0]


def enable_live_camera_poses(scene_cfg):
    for value in vars(scene_cfg).values():
        if hasattr(value, "update_latest_camera_pose"):
            value.update_latest_camera_pose = True


def refresh_reset_images(env, observation):
    """Discard stale camera caches after reset without consuming physics steps."""
    if not observation.get("image_obs"):
        return observation
    env.sim.forward()
    env.sim.render()
    for name in observation["image_obs"]:
        env.scene[name].update(0., force_recompute=True)
    return {**observation, "image_obs": env.observation_manager.compute_group("image_obs")}


def compact_gpu_buffers(cfg):
    budgets = {"gpu_temp_buffer_capacity": 16 * 1024**2,
               "gpu_heap_capacity": 64 * 1024**2, "gpu_collision_stack_size": 64 * 1024**2,
               "gpu_max_rigid_contact_count": 2**20, "gpu_max_rigid_patch_count": 2**15,
               "gpu_found_lost_pairs_capacity": 2**16,
               "gpu_found_lost_aggregate_pairs_capacity": 2**16,
               "gpu_total_aggregate_pairs_capacity": 2**18}
    for name, value in budgets.items():
        setattr(cfg.sim.physx, name, value)
