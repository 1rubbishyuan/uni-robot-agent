"""Single-episode Isaac service. All simulator calls run on its main thread."""

import argparse
import os
import sys
import threading
import traceback
from concurrent.futures import Future
from pathlib import Path
from queue import Empty, Queue

from uni_robot_agent.integrations.robodojo.environment import native_compat


class _OwnerThreadBackend:
    """Marshal native backend operations onto Isaac's owner thread.

    Request workers wait for each queued operation's result while the main
    thread pumps the queue. Tool-level motion loops remain on request workers.
    """

    def __init__(self, backend):
        self.backend, self.description = backend, backend.description
        self._owner = threading.get_ident()
        self._queue = Queue()

    def _call(self, operation, *args, **kwargs):
        if threading.get_ident() == self._owner:
            return operation(*args, **kwargs)
        result = Future()
        self._queue.put((operation, args, kwargs, result))
        # Physical operations are never retried after an uncertain outcome.
        return result.result()

    def pump(self, timeout=.1):
        if threading.get_ident() != self._owner:
            raise RuntimeError("Only the native owner thread can drain its queue")
        try:
            operation, args, kwargs, result = self._queue.get(timeout=timeout)
        except Empty:
            return
        try:
            result.set_result(operation(*args, **kwargs))
        except BaseException as error:  # noqa: BLE001 - preserve native thread failures for the request caller.
            result.set_exception(error)

    def read_state(self):
        return self._call(self.backend.read_state)

    def step_pose(self, command):
        return self._call(self.backend.step_pose, command)

    def step_joints(self, command):
        return self._call(self.backend.step_joints, command)

    def step_native(self, command):
        return self._call(self.backend.step_native, command)

    def validate_commands(self, commands, mode):
        return self._call(self.backend.validate_commands, commands, mode)

    def solve_goal_region(self, request):
        return self._call(self.backend.solve_goal_region, request)

    def preview_joints(self, commands):
        return self._call(self.backend.preview_joints, commands)

    def capture(self, cameras=None, *, high_resolution=False):
        return self._call(self.backend.capture, cameras, high_resolution=high_resolution)

    def finish(self, reason, status=None):
        return self._call(self.backend.finish, reason, status)

    def close(self):
        return self._call(self.backend.close)


def _serve(adapter, args, app, *, integration="robodojo"):
    from uni_robot_agent.artifacts.store import ArtifactStore
    from uni_robot_agent.services.environment.contracts import EpisodeInfo
    from uni_robot_agent.services.environment.routes import build_router
    from uni_robot_agent.services.environment.service import EnvironmentService
    from uni_robot_agent.services.hosting import HttpServer, create_app

    adapter.start()
    owner = _OwnerThreadBackend(adapter)
    info = EpisodeInfo(episode_id=args.run_id, task=args.task, environment=owner.description,
                       integration=integration, capabilities=["motion", "capture", "joint_prefix", "preview"],
                       metadata={"task_id": args.task, "seed": 0, "layout": str(args.layout),
                                 "layout_sha256": args.layout_sha256,
                                 "instruction": adapter.read_state().metadata["instruction"],
                                 "skill_namespace": "robodojo"})
    service = EnvironmentService(owner, info, ArtifactStore(args.output_dir))
    try:
        service.initialize()
        server = HttpServer(create_app(build_router(service)), host=args.host, port=args.port)
    except BaseException:
        service.close(reason="initialization_failed")
        raise
    host_errors = []
    def run_host():
        try:
            server.start()
        except BaseException as error:  # noqa: BLE001 - report host startup failure without losing the thread error.
            host_errors.append(error)
    host = threading.Thread(target=run_host, name="episode-http", daemon=True)
    host.start()
    try:
        while host.is_alive() and app.is_running():
            owner.pump()
    finally:
        server.request_stop()
        while host.is_alive():
            owner.pump()
        host.join()
        # Shutdown may wait for an accepted motion; keep its native queue alive.
        closed = Future()
        def close_service():
            try:
                service.close()
                closed.set_result(None)
            except BaseException as error:  # noqa: BLE001 - shutdown must propagate native cleanup failures.
                closed.set_exception(error)
        closer = threading.Thread(target=close_service, name="episode-shutdown")
        closer.start()
        while closer.is_alive():
            owner.pump()
        closer.join()
        closed.result()
    if host_errors:
        raise RuntimeError("Episode HTTP host failed") from host_errors[0]


def main(argv=None, *, integration="robodojo", runtime=native_compat):
    parser = argparse.ArgumentParser()
    parser.add_argument("--native-root", type=Path, help="Native RoboDojo checkout; official runs require the pinned clean source")
    parser.add_argument("--task", required=True)
    parser.add_argument("--layout", type=Path, required=True)
    parser.add_argument("--layout-sha256", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18760)
    parser.add_argument("--ik-joint-delta-guard", action=argparse.BooleanOptionalAction, default=True,
                        help="reject native IK joint deltas above 1 rad before execution (default: on)")
    parser.add_argument("--no-depth", action="store_true",
                        help="Capture calibrated RGB only; never acquire depth images")
    parser.add_argument("--no-camera-calibration", action="store_true",
                        help="With --no-depth, capture RGB without camera intrinsics or extrinsics")
    args = parser.parse_args(argv)
    if args.no_camera_calibration and not args.no_depth:
        parser.error("--no-camera-calibration requires --no-depth")
    try:
        native_root = runtime.resolve_native_root(args.native_root)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    args.layout = args.layout.expanduser().resolve()
    args.output_dir = args.output_dir.resolve()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    os.environ["ROBODOJO_RUN_ID"] = args.run_id
    os.environ["ROBODOJO_ARTIFACT_ROOT"] = str(args.output_dir / "native")
    # Both upstream trees define utils; RoboDojo must precede its embedded XPolicyLab.
    sys.path[:0] = [str(native_root), str(native_root / "XPolicyLab")]
    os.chdir(args.output_dir)
    from isaaclab.app import AppLauncher

    kit = args.output_dir / "kit"
    kit.mkdir(exist_ok=True)
    settings = {
        "/app/tokens/omni_cache": "cache",
        "/app/tokens/omni_global_cache": "global_cache",
        "/app/tokens/cache": "cache",
        "/app/tokens/appcache": "cache",
        "/UJITSO/datastore/localCachePath": "derived_data",
        "/app/userConfigPath": "user.config.json",
        "/log/file": "kit.log",
    }
    kit_args = " ".join(f"--{key}={kit / value}" for key, value in settings.items())
    app = None
    adapter = None
    try:
        app = AppLauncher(headless=True, enable_cameras=True, kit_args=kit_args).app
        from src.eval_client.physx_warning_monitor import get_monitor

        get_monitor().start(enabled=True)
        native = runtime.create_native(args.task, app, args.output_dir, root=native_root)
        adapter = runtime.create_adapter(native, args.output_dir, args.layout, args.layout_sha256,
                          episode_id=args.run_id, ik_joint_delta_guard=args.ik_joint_delta_guard, depth_enabled=not args.no_depth,
                          calibration_enabled=not args.no_camera_calibration)
        _serve(adapter, args, app, integration=integration)
    except BaseException:
        (args.output_dir / "server_error.txt").write_text(traceback.format_exc())
        raise
    finally:
        if adapter is not None:
            adapter.close()
        if app is not None:
            app.close()


if __name__ == "__main__":
    main()
