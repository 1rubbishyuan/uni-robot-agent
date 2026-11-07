"""Prepare one CALVIN task with pinned native configuration and success predicates."""
import argparse
import hashlib
from pathlib import Path

from uni_robot_agent.integrations.simulation import server_parser, serve_episode, source_root

REVISION = "1431a46bd36bde5903fb6345e68b5ccc30def666"
BENCHMARK_REVISION = "fa03f01f19c65920e18cf37398a9ce859274af76"


def parser():
    p = server_parser(__doc__, 18786)
    p.add_argument("--benchmark-root", type=Path, required=True)
    p.add_argument("--scene", choices=("A", "B", "C", "D"), default="D")
    reset = p.add_mutually_exclusive_group(required=True)
    reset.add_argument("--initial-state", type=Path, help="NPZ containing robot_obs and scene_obs; no pickle")
    reset.add_argument("--scene-reset", action="store_true", help="Native default reset, for integration probes only")
    p.add_argument("--max-steps", type=int, default=360)
    p.add_argument("--egl", action=argparse.BooleanOptionalAction, default=True)
    return p


def prepare(args):
    if args.seed < 0 or args.max_steps < 1:
        raise ValueError("Use a nonnegative seed and positive action budget")
    root = source_root(args.native_root, REVISION)
    benchmark = source_root(args.benchmark_root, BENCHMARK_REVISION)
    import hydra
    import numpy as np
    from omegaconf import OmegaConf
    import calvin_env
    from .adapter import CalvinAdapter

    if not Path(calvin_env.__file__).resolve().is_relative_to(root):
        raise ValueError("A different CALVIN environment was imported")
    annotations = OmegaConf.load(benchmark / "calvin_models/conf/annotations/new_playtable_validation.yaml")
    task_config = OmegaConf.load(root / "conf/tasks/new_playtable_tasks.yaml")
    if args.task not in annotations or args.task not in task_config.tasks:
        raise ValueError("Select an official CALVIN task with a validation instruction")
    initial = None
    state_sha = None
    if args.initial_state is not None:
        path = args.initial_state.expanduser().resolve(strict=True)
        state_sha = hashlib.sha256(path.read_bytes()).hexdigest()
        with np.load(path, allow_pickle=False) as data:
            initial = {key: np.array(data[key], dtype=float) for key in ("robot_obs", "scene_obs")}
        if (initial["robot_obs"].shape != (15,) or initial["scene_obs"].shape != (24,)
                or not all(np.isfinite(value).all() for value in initial.values())):
            raise ValueError("Expected finite robot_obs[15] and scene_obs[24]")
    with hydra.initialize_config_dir(config_dir=str(root / "conf"), version_base=None):
        config = hydra.compose(config_name="config_data_collection", overrides=[
            "cameras=static_and_gripper", f"scene=calvin_scene_{args.scene}", "use_vr=false",
            f"env.seed={args.seed}", f"env.use_egl={str(args.egl).lower()}", "env.use_scene_info=true"])
        # Resolve relative assets against the validated source, not the caller's cwd.
        config.data_path = str(root / "data")
        env = hydra.utils.instantiate(config.env)
    try:
        observation = env.reset(**(initial or {}))
        oracle = hydra.utils.instantiate(task_config)
        metadata = {"benchmark": "calvin", "native_revision": REVISION,
                    "benchmark_revision": BENCHMARK_REVISION, "task_id": args.task,
                    "scene": args.scene, "seed": args.seed, "max_steps": args.max_steps,
                    "initial_state_sha256": state_sha,
                    "reset_source": "explicit_state" if initial is not None else "native_scene_default",
                    "evaluation_protocol": "single_task", "renderer": "egl" if args.egl else "tiny",
                    "evaluation_profile": "direct-rgbd" if args.depth else "direct-rgb"}
        return CalvinAdapter(env, oracle, task=args.task, instruction=str(annotations[args.task][0]),
                             episode_id=args.run_id, metadata=metadata, observation=observation,
                             max_steps=args.max_steps, depth_enabled=args.depth)
    except BaseException:
        env.close()
        raise


def main():
    args = parser().parse_args()
    serve_episode(lambda: prepare(args), args, "calvin")


if __name__ == "__main__":
    main()
