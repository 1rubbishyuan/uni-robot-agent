"""Prepare one pinned RoboUse task and serve the shared environment protocol."""
import argparse
import json
from pathlib import Path
from importlib.metadata import version

from ..tasks import load
from ..worker_tasks import family, bridge_spec
from .adapter import RoboUseAdapter
from uni_robot_agent.artifacts.store import ArtifactStore
from uni_robot_agent.services.environment.contracts import EpisodeInfo
from uni_robot_agent.services.environment.routes import build_router
from uni_robot_agent.services.environment.service import EnvironmentService
from uni_robot_agent.services.environment.owner import OwnedAdapter
from uni_robot_agent.services.hosting import create_app, serve


def create_episode(spec, run_dir, seed):
    from robouse.core.session import Episode
    episode = Episode.__new__(Episode)
    try:
        Episode.__init__(episode, spec, run_dir, seed=seed)
    except BaseException:
        # Episode may fail during its initial recording, after a child was opened.
        # No completed episode exists to grade; release only initialized resources.
        try:
            if getattr(episode, "backend", None) is not None:
                episode.backend.close()
        finally:
            if getattr(episode, "_trace", None) is not None:
                episode._trace.close()
            if getattr(episode, "video", None) is not None:
                episode.video.close(keep=False)
        raise
    return episode


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--task-dir", type=Path, required=True)
    p.add_argument("--backend", required=True)
    p.add_argument("--sim", choices=("maniskill", "genesis", "isaaclab"))
    p.add_argument("--worker-python", type=Path)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--seed", type=int)
    p.add_argument("--cameras", nargs="+", required=True)
    p.add_argument("--depth", action=argparse.BooleanOptionalAction, required=True)
    p.add_argument("--snapshot-interval", type=int, default=5)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=18780)
    p.add_argument("--check-only", action="store_true", help="Validate task/config without importing a simulator")
    return p


def main():
    args = parser().parse_args()
    task, instruction, digest = load(args.task_dir)
    if task.spec["backend"] != args.backend:
        raise ValueError("Task backend does not match the environment launch script")
    if args.sim != task.spec.get("sim"):
        raise ValueError("GPU simulator family does not match the launch script")
    if not args.cameras or len(args.cameras) != len(set(args.cameras)):
        raise ValueError("Configure at least one camera, with no duplicate names")
    if args.snapshot_interval < 1:
        raise ValueError("Snapshot interval must be positive")
    config = {"task_id": task.id, "task_sha256": digest, "robouse_version": "0.4.1", "backend": args.backend,
              "seed": args.seed if args.seed is not None else task.spec.get("seed", 0),
              "cameras": args.cameras, "depth_enabled": args.depth,
              "evaluation_profile": "uni-robot-agent-rgb-proprio", "worker_family": family(task.spec), "task_budget_s": task.agent_timeout_s}
    if args.check_only:
        print(json.dumps(config, indent=2))
        return
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / "config.json").write_text(json.dumps(config, indent=2) + "\n")

    native_spec = task.spec
    if family(native_spec):
        native_spec = bridge_spec(native_spec, args.worker_python, args.cameras, args.output_dir)
        from robouse.backends import backend_class
        backend_class("uni_robot_agent_worker")  # Verify the installed entry point before creating a process.

    def prepare():
        expected = "1.4.0" if args.backend == "libero" else "1.5.2"
        if args.backend in {"libero", "robosuite"} and version("robosuite") != expected:
            raise ValueError(f"This backend requires robosuite=={expected} in a separate environment")
        episode = create_episode(native_spec, args.output_dir / "native", args.seed)
        try:
            return RoboUseAdapter(episode, episode_id=args.run_id, cameras=args.cameras,
                                  depth_enabled=args.depth, snapshot_interval=args.snapshot_interval)
        except BaseException:
            episode.close()
            raise

    adapter = OwnedAdapter(prepare)
    artifacts = ArtifactStore(args.output_dir / "artifacts")
    service = None
    try:
        info = EpisodeInfo(episode_id=args.run_id, task=instruction, integration="robouse",
                           environment=adapter.description, metadata=config,
                           capabilities=["pose", "published_observations"])
        service = EnvironmentService(adapter, info, artifacts)
        service.initialize()
        serve(create_app(build_router(service)), host=args.host, port=args.port)
    finally:
        if service:
            service.close()
        else:
            adapter.close()
            artifacts.close()


if __name__ == "__main__":
    main()
