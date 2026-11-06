"""Prepare one native RoboLab episode and host the shared environment service."""
import argparse
import json
import math
from pathlib import Path
import signal
import threading
from uuid import uuid4


def positive_float(value):
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError("Value must be finite and positive")
    return result


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--task", required=True)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--instruction-type", default="default")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18770)
    parser.add_argument("--gripper-torque-limit-nm", type=positive_float, default=16.5)
    parser.add_argument("--compact-gpu-buffers", action="store_true")
    parser.add_argument("--record-video", action="store_true")
    return parser


def serve_native(service, owner, *, host, port):
    """Pump native requests on the main thread while HTTP drains on shutdown."""
    from uni_robot_agent.services.environment.routes import build_router
    from uni_robot_agent.services.hosting import HttpServer, create_app
    server = HttpServer(create_app(build_router(service)), host=host, port=port)
    finished = threading.Event()
    errors = []

    def run_http():
        try:
            server.start()
        except BaseException as error:
            errors.append(error)
        finally:
            finished.set()

    previous = {sig: signal.signal(sig, lambda *_: server.request_stop())
                for sig in (signal.SIGINT, signal.SIGTERM)}
    thread = threading.Thread(target=run_http, name="robolab-http")
    thread.start()
    try:
        while not finished.is_set():
            owner.pump()
    finally:
        server.request_stop()
        # Active ASGI requests may still await native receipts. Keep the owner alive.
        while not finished.is_set():
            owner.pump()
        thread.join()
        try:
            service.close()
        finally:
            try:
                server.stop()
            finally:
                for sig, handler in previous.items():
                    signal.signal(sig, handler)
    if errors:
        raise RuntimeError("RoboLab HTTP service failed") from errors[0]


def main(argv=None):
    parser = build_parser()
    parser.parse_known_args(argv)  # --help exits before importing Isaac or Torch.
    from .native_compat import check_version
    check_version()
    import cv2  # noqa: F401 -- upstream requires OpenCV before Isaac startup.
    from isaaclab.app import AppLauncher
    AppLauncher.add_app_launcher_args(parser)
    args = parser.parse_args(argv)
    args.enable_cameras = True
    app = AppLauncher(args).app
    env = adapter = artifacts = service = video = None
    from .executor import MainThreadExecutor
    owner = MainThreadExecutor()
    try:
        import torch
        import robolab.constants as constants
        from robolab.core.environments.factory import get_envs
        from robolab.core.environments.runtime import create_env, end_episode
        from robolab.core.environments.config import parse_env_cfg
        from robolab.registrations.droid.auto_env_registrations_jointpos import auto_register_droid_envs
        from isaaclab.envs.mdp import JointPositionActionCfg
        from uni_robot_agent.artifacts.store import ArtifactStore
        from uni_robot_agent.services.environment.contracts import EpisodeInfo
        from uni_robot_agent.services.environment.service import EnvironmentService
        from .adapter import RoboLabAdapter
        from .drives import drive_parameters
        from .kinematics import DroidModel
        from .native_compat import (ROBO_LAB_REVISION, array, benchmark_task_file, compact_gpu_buffers,
                                    enable_live_camera_poses, refresh_reset_images)

        args.output_dir = args.output_dir.resolve()
        args.output_dir.mkdir(parents=True, exist_ok=False)
        constants.set_output_dir(str(args.output_dir / "native"))
        task_file = benchmark_task_file(args.task, constants.TASK_DIR)
        auto_register_droid_envs(task_dirs=None, task=str(task_file))
        matches = get_envs(task=args.task)
        if len(matches) != 1:
            raise ValueError(f"Expected one registered native task; got {matches}")
        cfg = parse_env_cfg(matches[0], device=args.device, seed=args.seed, num_envs=1)
        enable_live_camera_poses(cfg.scene)
        cfg.scene.robot.actuators["gripper"].effort_limit_sim = args.gripper_torque_limit_nm
        if args.compact_gpu_buffers:
            compact_gpu_buffers(cfg)
        cfg.actions.finger_joint = JointPositionActionCfg(asset_name="robot", joint_names=["finger_joint"],
                                                         scale=math.pi/4, use_default_offset=False)
        env, cfg = create_env(cfg, device=args.device, seed=args.seed, num_envs=1,
                              instruction_type=args.instruction_type, policy="uni-robot-agent")
        finger, _ = env.scene["robot"].find_joints("finger_joint")
        applied = float(array(env.scene["robot"].root_physx_view.get_dof_max_forces())[0, finger[0]])
        if abs(applied - args.gripper_torque_limit_nm) > 1e-5:
            raise RuntimeError("PhysX did not apply the requested gripper torque limit")
        usd = Path(constants.ROBOTS_DIR) / "franka_robotiq_2f_85_flattened.usd"
        with usd.open("rb") as stream:
            if stream.read(80).startswith(b"version https://git-lfs.github.com/spec/"):
                raise ValueError("DROID USD is a Git LFS pointer; materialize native assets first")
        model = DroidModel(usd)
        metadata = {"task": args.task, "seed": args.seed, "instruction_type": args.instruction_type,
            "registered_env": matches[0], "robolab_version": "0.3.1",
            "supported_upstream_revision": ROBO_LAB_REVISION,
            "native_control_mode": "joint_position", "public_control_mode": "shared_gripper_primitives",
            "native_output_dir": str(args.output_dir / "native"),
            "gripper_torque_limit_nm": applied, "drive_parameters": drive_parameters(env.scene["robot"], model.arm_names),
            "gpu_buffer_profile": "compact" if args.compact_gpu_buffers else "native"}
        (args.output_dir / "config.json").write_text(json.dumps(metadata, indent=2))
        if args.record_video:
            from .video import EpisodeVideo
            video = EpisodeVideo(args.output_dir / "native", env.step_dt)

        def finish_native(current):
            try:
                end_episode(current)
            finally:
                if video is not None:
                    video.close()

        adapter = RoboLabAdapter(env, model, episode_id=args.run_id or uuid4().hex,
            metadata=metadata, output_dir=args.output_dir / "native", owner=owner,
            end_episode=finish_native, refresh_images=lambda obs: refresh_reset_images(env, obs),
            record_frame=video.add if video is not None else None)
        with torch.inference_mode():
            adapter.reset()
            artifacts = ArtifactStore(args.output_dir / "artifacts")
            info = EpisodeInfo(episode_id=adapter.episode_id, task=cfg.instruction, integration="robolab",
                environment=adapter.description, metadata=metadata,
                capabilities=["pose", "joints", "joint_preview", "published_observations"])
            service = EnvironmentService(adapter, info, artifacts)
            service.initialize()
            serve_native(service, owner, host=args.host, port=args.port)
    finally:
        try:
            if service is not None:
                service.close()
            elif adapter is not None:
                try:
                    if adapter._obs is not None:
                        adapter.finish("server_shutdown")
                finally:
                    adapter.close()
            elif env is not None:
                env.close()
        finally:
            try:
                if video is not None:
                    video.close()
                if artifacts is not None:
                    artifacts.close()
            finally:
                owner.close()
                app.close()


if __name__ == "__main__":
    main()
