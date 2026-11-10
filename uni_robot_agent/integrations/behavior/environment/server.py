"""Prepare one native BEHAVIOR episode; connecting clients never reset it."""
import json
from pathlib import Path

from uni_robot_agent.integrations.simulation import server_parser



def parser():
    result = server_parser(__doc__, 18789)
    result.add_argument("--instance-index", type=int, default=0)
    result.add_argument("--evaluation-mode", choices=("train", "public_test", "hidden_test"), default="public_test")
    return result


def prepare(args):
    if args.depth or args.seed < 0 or args.instance_index < 0:
        raise ValueError("BEHAVIOR requires RGB-only observations and nonnegative seed/instance")
    from .adapter import BehaviorAdapter
    from .native_compat import NativeEpisode
    native = NativeEpisode(args.native_root, task=args.task, seed=args.seed,
                           instance_index=args.instance_index, mode=args.evaluation_mode)
    try:
        return BehaviorAdapter(native, episode_id=args.run_id)
    except BaseException:
        try:
            native.close()
        finally:
            native.shutdown()
        raise


def main():
    from uni_robot_agent.artifacts.store import ArtifactStore
    from uni_robot_agent.services.environment.device_contracts import DeviceEpisodeInfo
    from uni_robot_agent.services.environment.main_thread import NativeOwner, serve_native
    from uni_robot_agent.services.environment.routes import build_router
    from uni_robot_agent.services.environment.service import EnvironmentService
    from uni_robot_agent.services.hosting import HttpServer, create_app

    args = parser().parse_args()
    output = Path(args.output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / "config.json").write_text(json.dumps(vars(args), default=str, indent=2) + "\n")
    adapter = NativeOwner(prepare(args))
    artifacts = service = None
    try:
        instruction, metadata = adapter.episode_identity()
        artifacts = ArtifactStore(output / "artifacts")
        info = DeviceEpisodeInfo(episode_id=args.run_id, task=instruction, integration="behavior",
            protocol_version="devices-2", tool_schema_version="devices-2",
            environment=adapter.description, metadata=metadata,
            capabilities=["coordinated_control", "published_observations"])
        service = EnvironmentService(adapter, info, artifacts)
        service.initialize()
        server = HttpServer(create_app(build_router(service)), host=args.host, port=args.port)
        serve_native(adapter, service, server, is_running=adapter.backend.native.og.app.is_running)
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
            adapter.backend.native.shutdown()


if __name__ == "__main__":
    main()
