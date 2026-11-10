"""Adopt pinned external Isaac runtimes; never silently migrate their physics."""
from pathlib import Path
import subprocess
import json
import os

from uni_robot_agent.services.deployment.config import LaunchPlan, Recipe
from uni_robot_agent.services.deployment.runtime import check_python

from .environment.native_compat import episode_index


def recipe(args):
    return Recipe.load(__package__, "deployment.toml")


def validate(args):
    if getattr(args, "task", None):
        episode_index(args.task)
    if args.command != "setup" and not getattr(args, "task", None):
        raise ValueError("NaVILA deployment requires --task episode:<index>")
    if getattr(args, "depth", False):
        raise ValueError("NaVILA exposes front RGB without depth")
    if any(getattr(args, key, None) for key in ("layout", "task_dir")):
        raise ValueError("NaVILA uses the pinned native episode dataset")
    if getattr(args, "runtime_profile", None) not in {None, "isaac41", "isaac51"}:
        raise ValueError("NaVILA runtime profile must be isaac41 or isaac51")
    cameras = getattr(args, "cameras", None)
    if cameras is not None and cameras != ["front"]:
        raise ValueError("NaVILA exposes only the front camera")
    if args.command == "setup" and not args.runtime_python:
        raise ValueError("NaVILA setup adopts prepared --runtime-python and --worker-python environments; "
                         "install the official Isaac runtime separately")


def external_data(args):
    if not args.native_root or not args.worker_python:
        raise ValueError("NaVILA adoption requires --native-root and --worker-python")
    return {"native_root": str(args.native_root.expanduser().resolve()),
            "worker_python": str(args.worker_python.expanduser().resolve()),
            "runtime_profile": args.runtime_profile or "isaac41"}


def prepare(context):
    raise ValueError("NaVILA requires adoption of a separately prepared pinned Isaac runtime")


def verify(runtime):
    info = check_python(runtime.python, ["numpy", "PIL"], environment=runtime.environment)
    code = """import importlib.util,json,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('navila_native',sys.argv[1])
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
profile=sys.argv[3]
if profile == 'isaac51':
    sys.path.insert(0,str(Path(sys.argv[1]).parent))
    from isaac51 import load_prepared
    load_prepared(sys.argv[2])
    assert sys.version_info[:2] == (3,11), 'Isaac Sim 5.1 requires Python 3.11'
else:
    module.validate_source(sys.argv[2])
    assert sys.version_info[:2] == (3,10), 'Isaac Sim 4.1 requires Python 3.10'
print(json.dumps(module.preflight(profile)))
"""
    result = subprocess.run([runtime.data["worker_python"], "-c", code,
        str(Path(__file__).parent / "environment/native_compat.py"), runtime.data["native_root"],
        runtime.data.get("runtime_profile", "isaac41")],
        text=True, capture_output=True, timeout=30,
        env={**os.environ, **runtime.environment, "PYTHONPATH": "", "PYTHONNOUSERSITE": "1"})
    if result.returncode:
        raise RuntimeError("NaVILA native preflight failed: " + result.stderr[-3000:])
    return {**info, "native": json.loads(result.stdout.strip().splitlines()[-1])}


def plan(args, runtime, output, run_id):
    validate(args)
    profile = runtime.data.get("runtime_profile", "isaac41")
    if args.runtime_profile and args.runtime_profile != profile:
        raise ValueError("Launch profile differs from the adopted native runtime")
    port = args.port or 18790
    command = (runtime.python, "-m", "uni_robot_agent.integrations.navila.environment.server",
        "--native-root", runtime.data["native_root"], "--worker-python", runtime.data["worker_python"],
        "--runtime-profile", profile, "--task", args.task, "--run-id", run_id, "--seed", str(args.seed or 0), "--no-depth",
        "--output-dir", str(output / "environment"), "--host", args.host, "--port", str(port))
    host = "127.0.0.1" if args.host == "0.0.0.0" else args.host
    return LaunchPlan(command, f"http://{host}:{port}",
        {"integration": "navila", "episode_id": run_id, "metadata.task_id": args.task}, runtime.environment, output)
