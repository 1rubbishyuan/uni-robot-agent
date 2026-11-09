"""Prepare and launch a pinned native OpenPI runtime."""
import json
from pathlib import Path

from uni_robot_agent.services.deployment.config import LaunchPlan
from uni_robot_agent.services.deployment.runtime import check_python, query_python

from .config import recipe_settings


def validate(args):
    if not args.profile:
        raise ValueError("OpenPI deployment requires an explicit profile")


def external_data(args):
    if not args.native_root or not args.checkpoint:
        raise ValueError("External actor runtime requires --native-root and --checkpoint")
    return {"native_root": str(args.native_root.expanduser().resolve()),
            "checkpoint": str(args.checkpoint.expanduser().resolve()),
            "checkpoint_sha256": args.checkpoint_sha256, "profile": args.profile}


def prepare(context):
    settings = context.recipe.settings
    source = Path(context.step("source", lambda: context.git(
        settings["repository"], settings["revision"], context.root / "source",
        submodules=settings.get("submodules", False))))
    project = source / settings["project"]
    if settings["installer"] == "uv-sync":
        context.step("uv", lambda: context.pip(f"uv=={settings['uv_version']}"))
        groups = [arg for group in settings.get("uv_groups", []) for arg in ("--group", group)]
        context.step("openpi", lambda: context.command(
            [str(context.python.parent / "uv"), "sync", "--frozen", *groups,
             "--no-dev", "--python", str(context.python)], cwd=project,
            environment={"UV_PROJECT_ENVIRONMENT": str(context.python.parent.parent)}))
        context.step("pip-bootstrap", lambda: context.command(
            [str(context.python), "-m", "ensurepip", "--upgrade"]))
    elif settings["installer"] == "pip":
        context.step("openpi", lambda: context.pip(*settings.get("packages", []),
            options=("--editable", str(project))))
        # The pinned client declares an incompatible NumPy requirement. Its tested
        # source runs with the model's NumPy; the recipe names the metadata exception.
        context.step("openpi-client", lambda: context.pip(
            str(source / settings["client_project"]), options=("--no-deps", "--editable")))
    else:
        raise ValueError(f"Unknown OpenPI installer: {settings['installer']}")
    if context.args.checkpoint:
        checkpoint = context.args.checkpoint.expanduser().resolve()
    else:
        download = Path(context.step("weights", lambda: context.snapshot(
            settings["asset_repository"], settings["asset_revision"], context.root / "weights",
            repo_type=settings.get("asset_repo_type", "dataset"), patterns=settings["asset_patterns"])))
        normalizer = f"assets/{settings['actor']['assets_id']}/norm_stats.json"
        candidates = [path.parent.parent.parent for path in download.rglob(normalizer)
                      if (path.parent.parent.parent / "params").is_dir()]
        if len(candidates) != 1:
            raise ValueError("Expected one recipe checkpoint; use --checkpoint to select it")
        checkpoint = candidates[0]
    context.data.update(native_root=str(source), checkpoint=str(checkpoint),
                        checkpoint_sha256=context.args.checkpoint_sha256, profile=context.args.profile)


def verify(runtime):
    info = check_python(runtime.python, ["jax"], environment=runtime.environment)
    # query_python is an isolated startup process, so setting its cwd cannot race
    # service threads. DexJoCo's pinned training-config module reads config.yaml.
    code = '''import json,os,sys
from pathlib import Path
from uni_robot_agent.services.actor.backends.openpi.config import resolve_config
from uni_robot_agent.services.actor.backends.openpi.native_compat import import_native,identify_checkpoint,validate_model_config
values=json.loads(sys.argv[1])
values["checkpoint_sha256"]="0"*64
settings=resolve_config(values)
if settings.working_directory is not None:
    os.chdir(Path(settings.native_root)/settings.working_directory)
source=import_native(settings)
from openpi.training import config
cfg=config.get_config(settings.model_config_name)
validate_model_config(cfg,settings.profile)
print(json.dumps({**identify_checkpoint(settings),**source}))
'''
    values = {key: runtime.data[key] for key in ("native_root", "checkpoint")}
    values["profile"] = runtime.data.get("profile", "robodojo_pi05")
    identity = query_python(runtime.python, code, json.dumps(values),
                            environment=runtime.environment, timeout=900)
    expected = runtime.data.get("checkpoint_sha256")
    if expected and expected != identity["checkpoint_sha256"]:
        raise ValueError("Actor checkpoint checksum mismatch")
    runtime.data["checkpoint_sha256"] = identity["checkpoint_sha256"]
    info.update({key: identity[key] for key in ("checkpoint_sha256", "source_sha256", "source_files_sha256")})
    return info


def plan(args, runtime, output, run_id):
    port = args.port or 18761
    data = runtime.data
    selected = data.get("profile", args.profile)
    if selected != args.profile:
        raise ValueError("Actor runtime profile differs from the requested recipe")
    settings = recipe_settings(selected)["actor"]
    command = [runtime.python, "-m", "uni_robot_agent.services.actor.server", "--backend", "openpi",
        "--profile", selected, "--native-root", data["native_root"], "--checkpoint", data["checkpoint"],
        "--checkpoint-sha256", data["checkpoint_sha256"], "--host", args.host, "--port", str(port)]
    host = "127.0.0.1" if args.host == "0.0.0.0" else args.host
    cwd = Path(data["native_root"]) / settings["working_directory"] if settings.get("working_directory") else output
    return LaunchPlan(tuple(command), f"http://{host}:{port}",
        {"model": settings["model"], "checkpoint_sha256": data["checkpoint_sha256"]}, runtime.environment, cwd)
