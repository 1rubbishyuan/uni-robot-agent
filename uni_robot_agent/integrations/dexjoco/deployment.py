"""Prepare an isolated pinned DexJoCo simulator and launch one admitted episode."""
from pathlib import Path

from uni_robot_agent.services.deployment.config import LaunchPlan, Recipe
from uni_robot_agent.services.deployment.runtime import check_python, query_python

from .environment.native_compat import TASK


def recipe(args):
    return Recipe.load(__package__, "deployment.toml")


def validate(args):
    if getattr(args, "task", None) not in (None, TASK):
        raise ValueError("DexJoCo currently admits pick_bucket only")
    if args.command != "setup" and not getattr(args, "task", None):
        raise ValueError("DexJoCo deployment requires --task pick_bucket")
    if getattr(args, "runtime_profile", None) or getattr(args, "worker_python", None):
        raise ValueError("DexJoCo uses one isolated native simulator runtime")
    if getattr(args, "depth", False):
        raise ValueError("DexJoCo currently exposes RGB observations only")
    if getattr(args, "layout", None) or getattr(args, "task_dir", None):
        raise ValueError("DexJoCo uses its pinned task definition and seeded native reset")
    cameras = getattr(args, "cameras", None)
    if cameras is not None and (len(cameras) != 2 or set(cameras) != {"front", "wrist"}):
        raise ValueError("DexJoCo requires front and wrist cameras")
    seed = getattr(args, "seed", None)
    if seed is not None and seed < 0:
        raise ValueError("DexJoCo seed must be nonnegative")


def external_data(args):
    if not args.native_root:
        raise ValueError("External DexJoCo runtime requires --native-root")
    return {"native_root": str(args.native_root.expanduser().resolve())}


def prepare(context):
    settings = context.recipe.settings
    root = context.step("source", lambda: context.git(
        settings["repository"], settings["revision"], context.root / "source"))
    context.step("native", lambda: context.pip("-e", str(Path(root) / "dexjoco")))
    context.data["native_root"] = root
    context.environment.update(MUJOCO_GL="egl", PYOPENGL_PLATFORM="egl")


def verify(runtime):
    info = check_python(runtime.python, ["mujoco", "gymnasium", "numpy", "scipy"],
                        environment=runtime.environment)
    script = """import importlib.metadata,json,sys
from uni_robot_agent.integrations.dexjoco.environment.native_compat import validate_source,REVISION
root=validate_source(sys.argv[1])
from dexjoco.tasks.pick_bucket import config
from pathlib import Path
assert Path(config.__file__).resolve().is_relative_to(root), 'Different DexJoCo source imported'
versions={name:importlib.metadata.version(name) for name in ['mujoco','gymnasium','numpy']}
assert versions == {'mujoco':'3.4.0','gymnasium':'1.0.0','numpy':'1.26.4'}, versions
print(json.dumps(dict(native_revision=REVISION,versions=versions)))
"""
    info.update(query_python(runtime.python, script, runtime.data["native_root"],
                             environment=runtime.environment))
    return info


def plan(args, runtime, output, run_id):
    validate(args)
    port = args.port or 18788
    seed = 0 if args.seed is None else args.seed
    command = (
        runtime.python, "-m", "uni_robot_agent.integrations.dexjoco.environment.server",
        "--native-root", runtime.data["native_root"], "--task", TASK,
        "--run-id", run_id, "--seed", str(seed), "--no-depth",
        "--output-dir", str(output / "environment"), "--host", args.host, "--port", str(port),
    )
    host = "127.0.0.1" if args.host == "0.0.0.0" else args.host
    return LaunchPlan(command, f"http://{host}:{port}",
        {"integration": "dexjoco", "episode_id": run_id, "metadata.task_id": TASK, "metadata.seed": seed},
        {"MUJOCO_GL": "egl", "PYOPENGL_PLATFORM": "egl", **runtime.environment}, output)
