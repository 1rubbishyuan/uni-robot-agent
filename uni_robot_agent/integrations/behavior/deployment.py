"""Adopt a separately installed BEHAVIOR runtime and launch one prepared task."""
from pathlib import Path

from uni_robot_agent.services.deployment.config import LaunchPlan, Recipe
from uni_robot_agent.services.deployment.runtime import check_python, query_python


def recipe(args):
    return Recipe.load(__package__, "deployment.toml")


def validate(args):
    if args.command != "setup" and not getattr(args, "task", None):
        raise ValueError("BEHAVIOR deployment requires --task")
    if getattr(args, "depth", False) or getattr(args, "layout", None) or getattr(args, "task_dir", None):
        raise ValueError("BEHAVIOR uses official task instances and RGB-only observations")
    if getattr(args, "worker_python", None) or getattr(args, "runtime_profile", None):
        raise ValueError("BEHAVIOR uses one native simulator interpreter")
    cameras = getattr(args, "cameras", None)
    if cameras is not None and (len(cameras) != 3 or set(cameras) != {"head", "left_wrist", "right_wrist"}):
        raise ValueError("BEHAVIOR requires its three native cameras")
    if getattr(args, "seed", None) is not None and args.seed < 0:
        raise ValueError("BEHAVIOR seed must be nonnegative")


def external_data(args):
    if not args.native_root:
        raise ValueError("BEHAVIOR runtime adoption requires --native-root")
    return {"native_root": str(args.native_root.expanduser().resolve())}


def prepare(context):
    raise ValueError("Install the pinned BEHAVIOR runtime and assets using its native setup, then adopt it with --runtime-python and --native-root; see docs/behavior.md")


def verify(runtime):
    info = check_python(runtime.python, ["omegaconf", "torch"], environment=runtime.environment)
    code = """import importlib.metadata,json,sys
from uni_robot_agent.integrations.behavior.environment.native_compat import validate_source,REVISION
root=validate_source(sys.argv[1])
version=importlib.metadata.version('isaacsim')
assert version in ('5.1.0','5.1.0.0'), version
print(json.dumps(dict(native_revision=REVISION,isaacsim=version)))
"""
    info.update(query_python(runtime.python, code, runtime.data["native_root"], environment=runtime.environment))
    return info


def plan(args, runtime, output, run_id):
    validate(args)
    port, seed = args.port or 18789, 0 if args.seed is None else args.seed
    instance = int(runtime.environment.get("BEHAVIOR_INSTANCE_INDEX", "0"))
    mode = runtime.environment.get("BEHAVIOR_EVALUATION_MODE", "public_test")
    if mode not in {"train", "public_test", "hidden_test"} or instance < 0 or (mode != "train" and instance >= 20):
        raise ValueError("Invalid BEHAVIOR instance index or evaluation split")
    command = (runtime.python, "-m", "uni_robot_agent.integrations.behavior.environment.server",
        "--native-root", runtime.data["native_root"], "--task", args.task, "--run-id", run_id,
        "--seed", str(seed), "--instance-index", str(instance), "--evaluation-mode", mode, "--no-depth",
        "--output-dir", str(output / "environment"), "--host", args.host, "--port", str(port))
    host = "127.0.0.1" if args.host == "0.0.0.0" else args.host
    return LaunchPlan(command, f"http://{host}:{port}",
        {"integration": "behavior", "episode_id": run_id, "metadata.task_id": args.task,
         "metadata.seed": seed, "metadata.instance_index": instance, "metadata.evaluation_mode": mode},
        runtime.environment, output)
