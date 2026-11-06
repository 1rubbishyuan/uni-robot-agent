"""Prepare a pinned RoboTwin episode and expose the shared environment service."""
import os
import re
from pathlib import Path

from uni_robot_agent.integrations.simulation import server_parser, serve_episode, source_root

REVISION = "ea8b21121ebb3cd201ff5b3fe361944ac94eda3f"


def parser():
    p = server_parser(__doc__, 18783)
    p.add_argument("--task-config", choices=("demo_clean", "demo_randomized"), default="demo_clean")
    p.add_argument("--instruction-type", choices=("seen", "unseen"))
    p.add_argument("--max-steps", type=int)
    return p


def prepare(args):
    if not re.fullmatch(r"[a-z][a-z0-9_]*", args.task) or args.seed < 0:
        raise ValueError("Use an upstream task identifier and nonnegative seed")
    if args.max_steps is not None and args.max_steps < 1:
        raise ValueError("--max-steps must be positive")
    root = source_root(args.native_root, REVISION)
    output = args.output_dir.expanduser().resolve()
    # Upstream asset and embodiment paths are relative to its checkout. This is
    # a dedicated native process; never change cwd in the planner process.
    os.chdir(root)
    from scripts import eval_policy_xpolicylab as native
    from .adapter import RoboTwinAdapter

    if not Path(native.__file__).resolve().is_relative_to(root):
        raise ValueError("A different RoboTwin runtime was imported")
    config, embodiment = native.load_task_args({"task_name": args.task, "task_config": args.task_config,
                                               "policy_name": "uni_robot_agent", "ckpt_setting": "direct"})
    if embodiment != "aloha_agilex":
        raise ValueError("This binding admits the official aloha_agilex parallel-gripper embodiment")
    config.update(eval_mode=True, render_freq=0, save_data=False, eval_video_save_dir=None,
                  save_path=str(output / "native"))
    config["data_type"] = {"rgb": True, "depth": args.depth, "endpose": True, "qpos": True}
    instruction_type = native.resolve_eval_instruction({"instruction_type": args.instruction_type}, config)
    env = native.class_decorator(args.task)
    try:
        # Apply the upstream expert seed-admission check, then reset the same seed
        # for the agent. No expert trajectories or state enter public metadata.
        env.setup_demo(now_ep_num=0, seed=args.seed, is_test=True, **config)
        expert_info = env.play_once()
        admissible = bool(env.plan_success and env.check_success())
    finally:
        env.close_env()
    if not admissible:
        raise ValueError("Seed rejected by upstream expert check; do not count as an agent failure")
    try:
        env.setup_demo(now_ep_num=0, seed=args.seed, is_test=True, **config)
        instruction = native.build_instruction(config, expert_info, instruction_type, 50, args.seed)
        env.set_instruction(instruction=instruction)
        if args.max_steps is not None:
            env.step_lim = args.max_steps
        return RoboTwinAdapter(env, episode_id=args.run_id, instruction=instruction, depth_enabled=args.depth,
            metadata={"task_id": args.task, "benchmark": "robotwin", "native_revision": REVISION,
                      "seed": args.seed, "task_config": args.task_config, "embodiment": embodiment,
                      "instruction_type": instruction_type, "expert_seed_check": True,
                      "max_steps": int(env.step_lim), "evaluation_profile": "direct-rgbd" if args.depth else "direct-rgb"})
    except BaseException:
        env.close_env()
        raise


def main():
    args = parser().parse_args()
    serve_episode(lambda: prepare(args), args, "robotwin")


if __name__ == "__main__":
    main()
