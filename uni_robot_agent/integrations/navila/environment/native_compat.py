"""Pinned NaVILA-Bench/IsaacLab boundary; no model-visible privileged state."""
import gzip
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import subprocess

import numpy as np

REVISION = "e9d2db12ce5788c0f987d734c0094100b6bc0d3a"
ISAACLAB_REVISION = "4d558ec83878c4892a46591c85ba91ac9d3c1834"
TASK = "go2_matterport_vision"
CONTROL_DT = .02
MAX_STEPS = 2500
POLICY = "logs/rsl_rl/go2_vision/2024-09-25_23-22-02/model_26499.pt"


def validate_source(root, revision=REVISION):
    root = Path(root).expanduser().resolve()
    current = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.run(["git", "-C", str(root), "diff", "--quiet", "HEAD", "--"]).returncode
    if current != revision or dirty:
        raise ValueError(f"Expected clean native source revision {revision}")
    return root


def preflight():
    # Inspect hardware without importing Torch, initializing CUDA or opening Isaac.
    result = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
                            text=True, capture_output=True, check=True)
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("NaVILA-Bench requires an RTX GPU for its native camera renderer")
    if any("blackwell" in line.lower() or re.search(r"RTX\s+50[56789]0", line) for line in lines):
        raise RuntimeError("Pinned NaVILA-Bench uses Isaac Sim 4.1, which does not support Blackwell GPUs. "
                           "An Isaac/runtime migration is required before simulation; no GPU launch was attempted.")
    version = importlib.metadata.version("isaacsim")
    if version.split(".")[:3] != ["4", "1", "0"]:
        raise ValueError("This integration admits the pinned Isaac Sim 4.1.0 runtime only")
    return {"isaacsim": version, "gpus": lines}


def episode_index(task):
    match = re.fullmatch(r"episode:(0|[1-9][0-9]*)", task)
    if match is None:
        raise ValueError("NaVILA task must be episode:<zero-based dataset index>")
    return int(match[1])


def load_episode(root, task):
    assets = Path(root) / "isaaclab_exts/omni.isaac.vlnce/assets"
    with gzip.open(assets / "vln_ce_isaac_v1.json.gz", "rt", encoding="utf-8") as stream:
        episodes = json.load(stream)["episodes"]
    index = episode_index(task)
    if index >= len(episodes):
        raise ValueError("NaVILA episode index exceeds the pinned dataset")
    return episodes[index], assets


class NativeEpisode:
    def __init__(self, env, application, episode, info, *, seed, policy_sha256):
        self.env, self.application, self.episode = env, application, episode
        self.info, self.seed, self.policy_sha256 = info, seed, policy_sha256
        self.closed = False

    @property
    def instruction(self):
        return self.episode["instruction"]["instruction_text"]

    def frame(self):
        return self.info["observations"]["camera_obs"][0, :, :, :3].detach().cpu().numpy().copy()

    def measured_state(self):
        robot = self.env.unwrapped.scene["robot"]
        return {"joint_positions_rad": dict(zip(robot.joint_names,
                    robot.data.joint_pos[0].detach().cpu().tolist(), strict=True)),
                "angular_velocity_body_rad_s": robot.data.root_ang_vel_b[0].detach().cpu().tolist()}

    def step(self, velocity):
        import torch
        with torch.inference_mode():
            _, _, done, self.info = self.env.step(torch.tensor(velocity, device=self.env.unwrapped.device))
        return bool(done)

    def finish(self, stopped):
        self.env.set_stop_called(stopped)
        self.env.measure_manager.update_measures()
        return {name: float(value) for name, value in self.env.measure_manager.get_measurements().items()}

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                self.env.close()
            finally:
                self.application.close()


def create_native(root, *, task, seed):
    root = validate_source(root)
    preflight()
    episode, assets = load_episode(root, task)
    policy_path = root / POLICY
    policy_sha256 = hashlib.sha256(policy_path.read_bytes()).hexdigest()
    if policy_path.stat().st_size < 1024:
        raise ValueError("Go2 locomotion checkpoint is missing or remains a Git LFS pointer")
    import inspect
    from omni.isaac.lab.app import AppLauncher
    lab_path = Path(inspect.getfile(AppLauncher)).parent
    lab_root = subprocess.check_output(["git", "-C", str(lab_path), "rev-parse", "--show-toplevel"], text=True).strip()
    validate_source(lab_root, ISAACLAB_REVISION)
    application = AppLauncher(headless=True, enable_cameras=True).app
    native = None
    try:
        import torch
        from omni.isaac.lab.envs import ManagerBasedRLEnv
        from omni.isaac.lab.utils import update_class_from_dict
        from omni.isaac.lab.utils.io import load_yaml
        from rsl_rl.runners import OnPolicyRunner
        import omni.isaac.vlnce.config.go2.go2_matterport_vision_cfg as configuration
        from omni.isaac.vlnce.utils import RslRlVecEnvHistoryWrapper, VLNEnvWrapper

        if not Path(inspect.getfile(OnPolicyRunner)).resolve().is_relative_to(root / "scripts/rsl_rl"):
            raise ValueError("Go2 requires the custom RSL-RL implementation from the pinned benchmark")
        if not Path(configuration.__file__).resolve().is_relative_to(root):
            raise ValueError("Imported a different NaVILA-Bench configuration")

        class TerminalPreservingEnv(ManagerBasedRLEnv):
            preserve_terminal = False

            def _reset_idx(self, env_ids):
                # IsaacLab resets before returning observations. After warmup, retain the
                # terminal scene for receipts and evaluation instead of starting another task.
                if not self.preserve_terminal:
                    super()._reset_idx(env_ids)

        cfg = configuration.Go2MatterportVisionCfg()
        scene = Path(episode["scene_id"]).stem
        cfg.scene.terrain.obj_filepath = str(assets / "matterport_usd" / scene / f"{scene}.usd")
        if not Path(cfg.scene.terrain.obj_filepath).is_file():
            raise ValueError("The selected NaVILA scene asset is missing")
        start, goal = episode["start_position"], episode["reference_path"][-1]
        cfg.scene.robot.init_state.pos = (*start[:2], start[2] + .4)
        cfg.scene.robot.init_state.rot = tuple(episode["start_rotation"])
        cfg.scene.terrain.origins = cfg.scene.robot.init_state.pos
        cfg.scene.disk_1.init_state.pos = (*start[:2], start[2] + 2.5)
        cfg.scene.disk_2.init_state.pos = (*goal[:2], goal[2] + 2.5)
        if not np.isclose(cfg.sim.dt * cfg.decimation, CONTROL_DT):
            raise ValueError("Pinned Go2 control period changed")
        TerminalPreservingEnv.seed(seed)
        native = TerminalPreservingEnv(cfg=cfg, render_mode=None)
        wrapped = RslRlVecEnvHistoryWrapper(native, history_length=9)
        agent = configuration.Go2VisionRoughPPORunnerCfg()
        update_class_from_dict(agent, load_yaml(policy_path.parent / "params/agent.yaml"))
        runner = OnPolicyRunner(wrapped, agent.to_dict(), log_dir=None, device=agent.device)
        runner.load(str(policy_path))
        policy = runner.get_inference_policy(device=native.device)
        env = VLNEnvWrapper(wrapped, policy, TASK, episode, max_length=MAX_STEPS,
            high_level_obs_key="camera_obs",
            measure_names=["PathLength", "DistanceToGoal", "Success", "SPL", "OracleNavigationError", "OracleSuccess"])
        with torch.inference_mode():
            _, info = env.reset()
        # The published Go2 wrapper warms up for 100 control steps before resetting metrics.
        native.preserve_terminal = True
        return NativeEpisode(env, application, episode, info, seed=seed, policy_sha256=policy_sha256)
    except BaseException:
        try:
            if native is not None:
                native.close()
        finally:
            application.close()
        raise
