"""Serve one prepared Arena device episode without connection-triggered resets."""
import json
from pathlib import Path

from uni_robot_agent.integrations.simulation import server_parser

from .native_compat import TASK


def prepare(args):
    if args.task != TASK or args.depth:
        raise ValueError("Select the pinned G1 locomanipulation task with RGB observations")
    from .adapter import ArenaAdapter
    from .native_compat import create_native
    env, app, observation, digest = create_native(args.native_root, seed=args.seed)
    try:
        return ArenaAdapter(env, app, observation, episode_id=args.run_id, seed=args.seed, reset_sha256=digest)
    except BaseException:
        try:
            env.close()
        finally:
            app.close()
        raise


def main():
    from uni_robot_agent.artifacts.store import ArtifactStore
    from uni_robot_agent.services.environment.device_contracts import DeviceEpisodeInfo
    from uni_robot_agent.services.environment.owner import OwnedAdapter
    from uni_robot_agent.services.environment.routes import build_router
    from uni_robot_agent.services.environment.service import EnvironmentService
    from uni_robot_agent.services.hosting import create_app, serve

    args = server_parser(__doc__, 18789).parse_args()
    output = Path(args.output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / "config.json").write_text(json.dumps(vars(args), default=str, indent=2) + "\n")
    adapter = OwnedAdapter(lambda: prepare(args))
    artifacts = service = None
    try:
        instruction, metadata = adapter.episode_identity()
        artifacts = ArtifactStore(output / "artifacts")
        info = DeviceEpisodeInfo(protocol_version="devices-2", tool_schema_version="devices-2",
            episode_id=args.run_id, task=instruction, integration="isaaclab_arena",
            environment=adapter.description, metadata=metadata,
            capabilities=["coordinated_control", "published_observations"])
        service = EnvironmentService(adapter, info, artifacts)
        service.initialize()
        serve(create_app(build_router(service)), host=args.host, port=args.port)
    finally:
        if service is not None:
            service.close()
        else:
            try:
                adapter.close()
            finally:
                if artifacts is not None:
                    artifacts.close()


if __name__ == "__main__":
    main()
