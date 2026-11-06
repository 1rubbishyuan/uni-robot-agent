"""Serve one LIBERO-Plus task with its official perturbations and initial state."""
from pathlib import Path

from uni_robot_agent.integrations.simulation import server_parser, serve_episode, source_root

REVISION = "4976dc30028e805ff8094b55501d532c48fec182"
BUDGETS = {"libero_spatial": 220, "libero_object": 280, "libero_goal": 300, "libero_10": 520}


def parser():
    p = server_parser(__doc__, 18781)
    p.add_argument("--suite", choices=tuple(BUDGETS), required=True)
    p.add_argument("--init-state-index", type=int, default=0)
    p.add_argument("--settle-steps", type=int, default=10)
    p.add_argument("--max-steps", type=int)
    return p


def prepare(args, *, benchmark_name="libero_plus", revision=REVISION):
    import os
    import yaml

    if args.seed < 0 or args.init_state_index < 0 or args.settle_steps < 1 or (args.max_steps is not None and args.max_steps < 1):
        raise ValueError("Seed/index must be nonnegative; step budgets must be positive")
    root = source_root(args.native_root, revision)
    # Each native package has the same import name. Use a separate service process
    # and a private config, never change another LIBERO installation's settings.
    config = args.output_dir.expanduser().resolve() / "libero-config"
    config.mkdir(parents=True)
    native = root / "libero/libero"
    (config / "config.yaml").write_text(yaml.safe_dump({
        "benchmark_root": str(native), "bddl_files": str(native / "bddl_files"),
        "init_states": str(native / "init_files"), "assets": str(native / "assets"),
        "datasets": str(root / "datasets"),
    }))
    os.environ["LIBERO_CONFIG_PATH"] = str(config)
    from libero.libero import benchmark
    from libero.libero.envs import OffScreenRenderEnv
    from robosuite import macros
    from importlib.metadata import version
    from .adapter import CAMERAS, LiberoAdapter

    if not Path(benchmark.__file__).resolve().is_relative_to(root):
        raise ValueError("A different LIBERO package is already imported; use a fresh service process")
    if version("robosuite") != "1.4.0":
        raise ValueError("Use robosuite==1.4.0 in the native service environment")
    if macros.IMAGE_CONVENTION not in {"opengl", "opencv"}:
        raise ValueError("Unsupported robosuite image convention")
    suite = benchmark.get_benchmark_dict()[args.suite](task_order_index=0)
    names = suite.get_task_names()
    index = int(args.task) if args.task.isdecimal() else names.index(args.task)
    if not 0 <= index < len(names):
        raise ValueError("Task index is outside the selected suite")
    task = suite.get_task(index)
    states = suite.get_task_init_states(index)
    if args.init_state_index >= len(states):
        raise ValueError("Initial-state index is outside the official task's states; no modulo wrapping")
    base_suite = next((name for name in BUDGETS if args.suite == name or args.suite.startswith(name + "_")), None)
    if base_suite is None and args.max_steps is None:
        raise ValueError("Supply --max-steps for a suite without a declared default")
    max_steps = args.max_steps or BUDGETS[base_suite]
    env = OffScreenRenderEnv(bddl_file_name=suite.get_task_bddl_file_path(index),
                            camera_names=list(CAMERAS), camera_heights=256, camera_widths=256,
                            camera_depths=args.depth, horizon=max_steps + args.settle_steps, ignore_done=True)
    try:
        env.seed(args.seed)
        env.reset()
        env.set_init_state(states[args.init_state_index])
        for _ in range(args.settle_steps):
            obs, _, _, _ = env.step([0, 0, 0, 0, 0, 0, -1])
        # The final settling observation has passed through the corruption wrapper.
        if env.check_success():
            raise ValueError("Task is already successful after settling; reject this initial state")
        return LiberoAdapter(env, obs, episode_id=args.run_id, instruction=task.language,
            metadata={"benchmark": benchmark_name, "native_revision": revision, "suite": args.suite,
                      "task_id": task.name, "task_index": index, "init_state_index": args.init_state_index,
                      "seed": args.seed, "settle_steps": args.settle_steps, "max_steps": max_steps,
                      "evaluation_profile": "direct-rgbd" if args.depth else "direct-rgb"},
            max_steps=max_steps, depth_enabled=args.depth, convention=macros.IMAGE_CONVENTION)
    except BaseException:
        env.close()
        raise


def main():
    args = parser().parse_args()
    serve_episode(lambda: prepare(args), args, "libero_plus")


if __name__ == "__main__":
    main()
