"""Prepare the harness bridge; the upstream GPU model is operated separately."""
import json

from uni_robot_agent.services.deployment.config import LaunchPlan
from uni_robot_agent.services.deployment.runtime import check_python


def validate(args):
    if args.profile != "navila_8b":
        raise ValueError("NaVILA bridge requires --profile navila_8b")
    if not args.checkpoint_sha256:
        raise ValueError("Declare the external NaVILA checkpoint with --checkpoint-sha256")
    if any(getattr(args, key, None) for key in ("native_root", "checkpoint", "worker_python")):
        raise ValueError("NaVILA uses an externally operated model server; configure its endpoint in runtime environment")


def external_data(args):
    validate(args)
    return {"profile": args.profile, "checkpoint_sha256": args.checkpoint_sha256}


def prepare(context):
    context.data.update(external_data(context.args))


def verify(runtime):
    info = check_python(runtime.python, ["numpy", "PIL"], environment=runtime.environment)
    return {**info, "native_model": "external", "identity_assurance": "operator_declared",
            "checkpoint_sha256": runtime.data["checkpoint_sha256"]}


def plan(args, runtime, output, run_id):
    from .config import resolve_config

    values = {"upstream_host": runtime.environment.get("NAVILA_UPSTREAM_HOST", "127.0.0.1"),
              "upstream_port": int(runtime.environment.get("NAVILA_UPSTREAM_PORT", "54321")),
              "checkpoint_sha256": runtime.data["checkpoint_sha256"], "exclusive_upstream": True}
    resolve_config(values)
    path = output / "navila-actor.toml"
    path.write_text('backend = "navila"\n' + "\n".join(
        f"{key} = {json.dumps(value)}" for key, value in values.items()) + "\n")
    port = args.port or 18763
    command = (runtime.python, "-m", "uni_robot_agent.services.actor.server", "--config", str(path),
               "--host", args.host, "--port", str(port))
    host = "127.0.0.1" if args.host == "0.0.0.0" else args.host
    return LaunchPlan(command, f"http://{host}:{port}",
        {"model": "navila-llama3-8b-8f", "checkpoint_sha256": values["checkpoint_sha256"]},
        runtime.environment, output)
