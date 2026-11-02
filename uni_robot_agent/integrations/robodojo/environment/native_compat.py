"""Pinned external RoboDojo boundary.

Supported native revision: ee67a1468510da7624a089164402359f2afc72c8.
The evaluator expects a policy client even for externally supplied joint actions.
Replace only its reset-only client during construction; never implement actor calls.
"""

from pathlib import Path
import subprocess

NATIVE_COMMIT = "ee67a1468510da7624a089164402359f2afc72c8"
# This per-call target-span guard is not a hardware limit or collision-free guarantee.
MAX_IK_JOINT_DELTA_RAD = 1.0


class NoPolicy:
    def __init__(self, **kwargs):
        pass

    def call(self, func_name, **kwargs):
        if func_name != "reset":
            raise RuntimeError("The native environment cannot call the Pi actor")

    def close(self):
        pass


def create_native(task, app, output, *, root):
    import yaml
    from omegaconf import OmegaConf
    from src.eval_client import eval_env
    from utils.pipeline_utils import process_config, process_randomization

    root = Path(root)

    def load(path):
        return yaml.safe_load(path.read_text())

    config_root = root / "env_cfg"
    evaluation = load(config_root / "arx_x5.yml")
    evaluation.update(
        task_name=task,
        num_envs=1,
        device_id=0,
        eval_batch=False,
        policy_name="direct",
        additional_info="uni_robot_agent",
        seed=0,
        eval_num=1,
        physx_monitor_enabled=True,
    )
    cfg = {
        name: load(config_root / name / (evaluation["config"][name] + ".yml"))
        for name in ("sim", "scene", "robot", "camera")
    }
    cfg.update(
        task_env=load(root / "task/RoboDojo/config" / (task + ".yml")),
        eval_cfg=evaluation,
        deploy_cfg={"port": 1, "host": "127.0.0.1"},
    )
    cfg = OmegaConf.create(cfg)
    cfg = process_randomization(cfg)
    cfg, _ = process_config(cfg, task_name=task)
    cfg.sim.scene.num_envs = 1
    cfg.sim.seed = [0]
    cfg.camera.default_frequency = 25
    cfg.eval_cfg.observation.collect_freq = 25
    original = eval_env.WsModelClient
    eval_env.WsModelClient = NoPolicy
    try:
        env = eval_env.create_eval_env(cfg, app)
    finally:
        eval_env.WsModelClient = original
    env.save_dir = str(Path(output) / "native")
    env._stream_dir = str(Path(env.save_dir) / "_stream")
    env.obs_manager.obs_config["save_dir"] = env.save_dir
    return env


def plan_eef_step(env, robots, action, *, ik_joint_delta_guard=False, slot=0, joint_holds=None, solve=None):
    """Validate native IK targets, optionally rejecting large joint deltas before physics."""
    import numpy as np

    commands, diagnostics = {}, {}
    joint_holds = joint_holds or {}
    for arm, robot in robots.items():
        if arm in joint_holds:
            commands[arm] = joint_holds[arm]
            diagnostics[arm] = {"held_joints": True}
            continue
        manager = env.robot_manager
        current = np.asarray(manager.get_joint(robot)[slot], dtype=float)
        result = (solve(arm, action[f"{arm}_ee_pose"], current) if solve is not None else
                  manager.solve_ik(target_pose=action[f"{arm}_ee_pose"], env_idx=slot, robot=robot))
        diagnostics[arm] = {"ik_status": result["status"], "current_joints": current.tolist(),
                            "ik_joint_delta_guard": ik_joint_delta_guard}
        if "recovery" in result:
            diagnostics[arm]["recovery"] = result["recovery"]
        if result["status"] != "Success":
            return {"accepted": False, "reason": result.get("reason", "ik_failed"), "arm": arm, "arms": diagnostics}
        target = np.asarray(result["joint_value"], dtype=float)
        if target.shape != current.shape or not np.isfinite(target).all():
            return {"accepted": False, "reason": "invalid_joint_target", "arm": arm, "arms": diagnostics}
        # Native interpolation uses raw joint angles; wrapping or clipping must not hide commanded spans.
        delta = target - current
        diagnostics[arm].update(
            target_joints=target.tolist(), joint_delta_rad=delta.tolist(),
            max_joint_delta_rad=float(np.max(np.abs(delta))),
            joint_delta_limit_rad=MAX_IK_JOINT_DELTA_RAD,
        )
        exceeded = np.flatnonzero(np.abs(delta) > MAX_IK_JOINT_DELTA_RAD)
        diagnostics[arm]["exceeded_joint_indices"] = exceeded.tolist()
        if ik_joint_delta_guard and exceeded.size:
            return {"accepted": False, "reason": "joint_jump_rejected", "arm": arm, "arms": diagnostics}
        commands[arm] = {"joints": target.tolist(), "gripper": float(action[f"{arm}_ee_joint_state"][0])}
    # Preserve native dual-arm solve order; execute only if both succeed, never reuse stale targets.
    return {"accepted": True, "commands": commands, "arms": diagnostics}


def native_call(env, operation, *, allow_released=False):
    """Execute on the standalone native owner selected by the caller."""
    return operation(env)


def resolve_native_root(root):
    """Accept only the pinned, unmodified official checkout and its submodules."""
    if root is None:
        raise ValueError("--native-root must point to the official RoboDojo checkout")
    root = Path(root).expanduser().resolve()
    command = ["git", "-c", f"safe.directory={root}", "--no-optional-locks", "-C", str(root)]

    def git(*args, strip=True):
        result = subprocess.run([*command, *args], capture_output=True, text=True)
        if result.returncode:
            raise ValueError(f"Cannot verify official RoboDojo checkout: {' '.join(args)}")
        return result.stdout.strip() if strip else result.stdout

    if Path(git("rev-parse", "--show-toplevel")).resolve() != root:
        raise ValueError("--native-root must be the RoboDojo Git checkout root")
    if git("rev-parse", "HEAD") != NATIVE_COMMIT:
        raise ValueError(f"Official RoboDojo requires commit {NATIVE_COMMIT}")
    git("diff", "--quiet", "HEAD", "--ignore-submodules=none")
    if git("ls-files", "--others", "--exclude-standard", "--", "*.py"):
        raise ValueError("Official RoboDojo contains untracked Python sources")
    modules = git("submodule", "status", "--recursive", strip=False)
    if any(line and line[0] != " " for line in modules.splitlines()):
        raise ValueError("Initialize the pinned RoboDojo submodules with git submodule update --init --recursive")
    if not (root / "env_cfg/arx_x5.yml").is_file():
        raise ValueError("RoboDojo env_cfg/arx_x5.yml is missing")
    return root


def create_adapter(*args, **kwargs):
    from .adapter import Adapter
    return Adapter(*args, **kwargs)
