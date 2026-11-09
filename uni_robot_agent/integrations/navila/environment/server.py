"""Prepare one native Go2 navigation episode behind the shared device service."""
import json
from pathlib import Path

from uni_robot_agent.integrations.simulation import server_parser


def parser():
    result = server_parser(__doc__, 18790)
    result.add_argument("--worker-python", type=Path, required=True,
                        help="Prepared Python 3.10 Isaac Sim 4.1 interpreter")
    return result


def prepare(args):
    from .adapter import NaVILAAdapter
    from .client import NativeClient
    from .native_compat import episode_index
    episode_index(args.task)
    if args.depth or args.seed < 0:
        raise ValueError("NaVILA requires RGB observations and a nonnegative seed")
    native = NativeClient(args.worker_python, args.native_root, task=args.task, seed=args.seed,
                          output=Path(args.output_dir) / "native")
    try:
        return NaVILAAdapter(native, episode_id=args.run_id, task=args.task)
    except BaseException:
        native.close()
        raise


def main():
    from uni_robot_agent.artifacts.store import ArtifactStore
    from uni_robot_agent.services.environment.device_contracts import DeviceEpisodeInfo
    from uni_robot_agent.services.environment.owner import OwnedAdapter
    from uni_robot_agent.services.environment.routes import build_router
    from uni_robot_agent.services.environment.service import EnvironmentService
    from uni_robot_agent.services.hosting import create_app, serve

    args = parser().parse_args()
    output = Path(args.output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / "config.json").write_text(json.dumps(vars(args), default=str, indent=2) + "\n")
    adapter = OwnedAdapter(lambda: prepare(args))
    artifacts = service = None
    try:
        instruction, metadata = adapter.episode_identity()
        artifacts = ArtifactStore(output / "artifacts")
        info = DeviceEpisodeInfo(episode_id=args.run_id, task=instruction, integration="navila",
            protocol_version="devices-2", tool_schema_version="devices-2",
            environment=adapter.description, metadata=metadata,
            capabilities=["coordinated_control", "published_observations", "camera_history"])
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
