"""Prepare the pinned native GR00T runtime using its official dependency lock."""
import hashlib
import json
from pathlib import Path

from uni_robot_agent.services.actor.config import load_recipe
from uni_robot_agent.services.deployment.config import LaunchPlan
from uni_robot_agent.services.deployment.runtime import check_python, query_python


def validate(args):
    if not args.profile:
        raise ValueError("GR00T deployment requires an explicit actor profile")
    if load_recipe(args.profile).settings.get("backend") != "gr00t":
        raise ValueError("Select a GR00T deployment recipe")


def external_data(args):
    validate(args)
    if not args.native_root or not args.checkpoint:
        raise ValueError("External GR00T runtime requires --native-root and --checkpoint")
    return {"native_root": str(args.native_root.expanduser().resolve()),
            "checkpoint": str(args.checkpoint.expanduser().resolve()),
            "checkpoint_sha256": args.checkpoint_sha256, "profile": args.profile}


def prepare(context):
    settings = context.recipe.settings
    source = context.step("source", lambda: context.git(settings["repository"], settings["revision"],
                                                        context.root / "source"))
    if hashlib.sha256((Path(source) / "uv.lock").read_bytes()).hexdigest() != settings["lock_sha256"]:
        raise ValueError("GR00T dependency lock differs from the verified inference recipe")
    context.step("uv", lambda: context.pip(f"uv=={settings['uv_version']}"))
    excluded = [argument for package in settings["excluded_packages"]
                for argument in ("--no-install-package", package)]
    context.step("gr00t", lambda: context.command(
        [str(context.python.parent / "uv"), "sync", "--frozen", "--no-dev", *excluded, "--python", str(context.python)],
        cwd=source, environment={"UV_PROJECT_ENVIRONMENT": str(context.python.parent.parent)}))
    context.step("pip-bootstrap", lambda: context.command([str(context.python), "-m", "ensurepip", "--upgrade"]))
    checkpoint = context.args.checkpoint
    if checkpoint is None:
        checkpoint = context.step("weights", lambda: context.snapshot(settings["asset_repository"],
            settings["asset_revision"], context.root / "weights", repo_type="model",
            patterns=settings["asset_patterns"]))
    context.data.update(native_root=str(source), checkpoint=str(Path(checkpoint).expanduser().resolve()),
                        checkpoint_sha256=context.args.checkpoint_sha256, profile=context.args.profile)


def verify(runtime):
    info = check_python(runtime.python, ["torch", "transformers"], environment=runtime.environment)
    script = """import json,os,shutil,subprocess,sys,torch
from pathlib import Path
from uni_robot_agent.services.actor.backends.gr00t.native_compat import import_native,identify_checkpoint
import_native(sys.argv[1])
compiler = os.environ.get("CC") or shutil.which("cc")
if not compiler:
 raise ValueError("GR00T/Triton requires a C compiler; supply CC in --runtime-env")
subprocess.run([compiler,"--version"],check=True,capture_output=True,text=True)
cuda_root = os.environ.get("CUDA_HOME")
if not cuda_root:
 raise ValueError("GR00T native dependencies require CUDA_HOME pointing to CUDA 12.8")
nvcc = Path(cuda_root) / "bin/nvcc"
cuda_version = subprocess.check_output([str(nvcc),"--version"],text=True)
if "release 12.8" not in cuda_version:
 raise ValueError("GR00T requires the CUDA 12.8 toolkit matching its pinned Torch build")
assert sys.version_info[:2] == (3,12), 'The GR00T recipe requires Python 3.12'
assert torch.__version__.split('+')[0] == '2.7.1', torch.__version__
assert torch.version.cuda == '12.8', torch.version.cuda
print(json.dumps(dict(checkpoint_sha256=identify_checkpoint(sys.argv[2]),torch=torch.__version__,cuda=torch.version.cuda,compiler=compiler,cuda_home=cuda_root)))
"""
    identity = query_python(runtime.python, script, runtime.data["native_root"], runtime.data["checkpoint"],
                            environment=runtime.environment, timeout=900)
    expected = runtime.data.get("checkpoint_sha256")
    if expected and identity["checkpoint_sha256"] != expected:
        raise ValueError("GR00T checkpoint checksum mismatch")
    runtime.data["checkpoint_sha256"] = identity["checkpoint_sha256"]
    return {**info, **identity}


def plan(args, runtime, output, run_id):
    validate(args)
    if runtime.data.get("profile") != args.profile:
        raise ValueError("Prepared GR00T runtime profile differs from the requested recipe")
    port = args.port or 18762
    data = runtime.data
    command = (runtime.python, "-m", "uni_robot_agent.services.actor.server", "--backend", "gr00t",
        "--profile", args.profile, "--native-root", data["native_root"], "--checkpoint", data["checkpoint"],
        "--checkpoint-sha256", data["checkpoint_sha256"], "--host", args.host, "--port", str(port))
    host = "127.0.0.1" if args.host == "0.0.0.0" else args.host
    settings = load_recipe(args.profile).settings["actor"]
    return LaunchPlan(command, f"http://{host}:{port}",
        {"model": settings["model"], "checkpoint_sha256": data["checkpoint_sha256"]}, runtime.environment, output)
