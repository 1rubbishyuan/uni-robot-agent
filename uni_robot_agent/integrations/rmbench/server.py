"""Prepare a pinned RMBench episode and expose the shared environment service."""
import os
import re
import random
from pathlib import Path

from uni_robot_agent.integrations.simulation import server_parser, serve_episode, source_root

REVISION = "7d1cdfc7661c273a7a5c5f6126e2d1a7f0a3f4b7"


def parser():
    p = server_parser(__doc__, 18785)
    p.add_argument("--task-config", choices=("demo_clean",), default="demo_clean")
    p.add_argument("--instruction-type", choices=("seen", "unseen"))
    p.add_argument("--max-steps", type=int)
    return p


def prepare(args):
    if not re.fullmatch(r"[a-z][a-z0-9_]*", args.task) or args.seed < 0:
        raise ValueError("Use an upstream task identifier and nonnegative seed")
    if args.max_steps is not None and args.max_steps < 1:
        raise ValueError("--max-steps must be positive")
    root = source_root(args.native_root, REVISION)
    random.seed(args.seed)
    output = args.output_dir.expanduser().resolve()
    # Upstream asset and embodiment paths are relative to its checkout. This is
    # a dedicated native process; never change cwd in the planner process.
    os.chdir(root)
    from scripts import eval_policy_xpolicylab as native
    from .adapter import RMBenchAdapter

    if not Path(native.__file__).resolve().is_relative_to(root):
        raise ValueError("A different RMBench runtime was imported")
    config, embodiment = native.load_task_args({"task_name": args.task, "task_config": args.task_config,
                                               "policy_name": "uni_robot_agent", "ckpt_setting": "direct"})
    if embodiment != "aloha-agilex":
        raise ValueError("This binding admits the official aloha-agilex parallel-gripper embodiment")
    config.update(eval_mode=True, render_freq=0, save_data=False, eval_video_save_dir=None,
                  save_path=str(output / "native"))
    config["data_type"] = {"rgb": True, "depth": args.depth, "endpose": True, "qpos": True, "third_view": True}
    instruction_type = native.resolve_eval_instruction({"instruction_type": args.instruction_type}, config)
    env = native.class_decorator(args.task)
    try:
        # Apply the upstream expert seed-admission check, then reset the same seed
        # for the agent. No expert trajectories or state enter public metadata.
        random.seed(args.seed)
        env.setup_demo(now_ep_num=0, seed=args.seed, is_test=True, **config)
        expert_info = env.play_once()
        planning_succeeded = bool(env.plan_success)
        task_succeeded = bool(env.check_success())
        admissible = planning_succeeded and task_succeeded
    finally:
        env.close_env()
    if not admissible:
        raise ValueError(
            f"Seed {args.seed} rejected by upstream expert check: "
            f"planning_succeeded={planning_succeeded}, task_succeeded={task_succeeded}; "
            "do not count as an agent failure"
        )
    try:
        random.seed(args.seed)
        env.setup_demo(now_ep_num=0, seed=args.seed, is_test=True, **config)
        instruction = native.build_instruction(config, expert_info, instruction_type, 50)
        env.set_instruction(instruction=instruction)
        if args.max_steps is not None:
            env.step_lim = args.max_steps
        if env.step_lim is None or env.step_lim < 1:
            raise ValueError("This native task needs an explicit positive --max-steps")
        return RMBenchAdapter(env, episode_id=args.run_id, instruction=instruction, depth_enabled=args.depth,
            metadata={"task_id": args.task, "benchmark": "rmbench", "native_revision": REVISION,
                      "seed": args.seed, "task_config": args.task_config, "embodiment": embodiment,
                      "instruction_type": instruction_type, "expert_seed_check": True,
                      "max_steps": int(env.step_lim), "evaluation_profile": "direct-rgbd" if args.depth else "direct-rgb"})
    except BaseException:
        env.close_env()
        raise


def main():
    args = parser().parse_args()
    serve_episode(lambda: prepare(args), args, "rmbench")


if __name__ == "__main__":
    main()
