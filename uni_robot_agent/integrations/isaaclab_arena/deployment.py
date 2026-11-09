"""Prepare a pinned Arena runtime; keep simulator requirements out of the planner."""
import subprocess

from uni_robot_agent.services.deployment.config import LaunchPlan, Recipe
from uni_robot_agent.services.deployment.runtime import query_python

from .environment.native_compat import TASK, validate_driver


def recipe(args):
    return Recipe.load(__package__, "deployment.toml")


def validate(args):
    if getattr(args, "task", None) not in (None, TASK):
        raise ValueError("The pinned Arena recipe admits galileo_g1_locomanip_pick_and_place")
    if args.command != "setup" and not getattr(args, "task", None):
        raise ValueError("Arena deployment requires its explicit task name")
    if getattr(args, "depth", False) or getattr(args, "sam3_url", None):
        raise ValueError("The admitted Arena profile exposes RGB only")
    if any(getattr(args, name, None) for name in ("runtime_profile", "worker_python", "layout", "task_dir")):
        raise ValueError("Arena uses one pinned native runtime and seeded task definition")
    if getattr(args, "cameras", None) not in (None, ["head"]):
        raise ValueError("The admitted Arena profile requires the native head camera")
    if getattr(args, "seed", None) is not None and args.seed < 0:
        raise ValueError("Arena seed must be nonnegative")


def external_data(args):
    if not args.native_root:
        raise ValueError("External Arena runtime requires --native-root")
    return {"native_root": str(args.native_root.expanduser().resolve())}


def prepare(context):
    # Fail before downloading a large incompatible runtime; never alter host drivers.
    drivers = subprocess.check_output(["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"], text=True).splitlines()
    if not drivers:
        raise ValueError("Arena preparation requires an NVIDIA GPU host")
    for driver in drivers:
        validate_driver(driver)
    settings = context.recipe.settings
    root = context.step("source", lambda: context.git(settings["repository"], settings["revision"],
                                                    context.root / "source", submodules=True))
    context.step("uv", lambda: context.pip(f"uv=={settings['uv_version']}"))
    context.step("arena", lambda: context.command(
        [str(context.python.parent / "uv"), "sync", "--locked", "--no-dev", "--python", str(context.python)],
        cwd=root, environment={"UV_PROJECT_ENVIRONMENT": str(context.python.parent.parent)}))
    context.step("pip-bootstrap", lambda: context.command([str(context.python), "-m", "ensurepip", "--upgrade"]))
    context.data["native_root"] = root


def verify(runtime):
    script = """import json,sys
from uni_robot_agent.integrations.isaaclab_arena.environment.native_compat import preflight
print(json.dumps(preflight(sys.argv[1])))
"""
    return query_python(runtime.python, script, runtime.data["native_root"], environment=runtime.environment)


def plan(args, runtime, output, run_id):
    validate(args)
    port, seed = args.port or 18789, 0 if args.seed is None else args.seed
    command = (runtime.python, "-m", "uni_robot_agent.integrations.isaaclab_arena.environment.server",
        "--native-root", runtime.data["native_root"], "--task", TASK,
        "--run-id", run_id, "--seed", str(seed), "--no-depth",
        "--output-dir", str(output / "environment"), "--host", args.host, "--port", str(port))
    host = "127.0.0.1" if args.host == "0.0.0.0" else args.host
    return LaunchPlan(command, f"http://{host}:{port}",
        {"integration": "isaaclab_arena", "episode_id": run_id, "metadata.task_id": TASK, "metadata.seed": seed},
        runtime.environment, output)
