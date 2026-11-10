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
    env, app, observation, digest = create_native(args.native_root, seed=args.seed, assets_dir=args.assets_dir)
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
    from uni_robot_agent.services.environment.main_thread import NativeOwner, serve_native
    from uni_robot_agent.services.environment.routes import build_router
    from uni_robot_agent.services.environment.service import EnvironmentService
    from uni_robot_agent.services.hosting import HttpServer, create_app

    parser = server_parser(__doc__, 18789)
    parser.add_argument("--assets-dir", type=Path, help="Prepared asset root containing the upstream Isaac directory")
    args = parser.parse_args()
    output = Path(args.output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / "config.json").write_text(json.dumps(vars(args), default=str, indent=2) + "\n")
    adapter = NativeOwner(prepare(args))
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
        server = HttpServer(create_app(build_router(service)), host=args.host, port=args.port)
        serve_native(adapter, service, server, is_running=adapter.backend.app.is_running)
    finally:
        try:
            if service is not None:
                service.close()
            else:
                try:
                    adapter.close()
                finally:
                    if artifacts is not None:
                        artifacts.close()
        finally:
            # Kit can terminate the process; HTTP and artifact shutdown go first.
            adapter.backend.app.close()


if __name__ == "__main__":
    main()
