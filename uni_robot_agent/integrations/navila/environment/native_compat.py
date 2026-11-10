"""Pinned NaVILA-Bench/IsaacLab boundary; no model-visible privileged state."""
import gzip
import hashlib
import importlib
import importlib.metadata
import json
from pathlib import Path
import re
import subprocess
import sys

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


def preflight(runtime_profile="isaac41"):
    # Inspect hardware without importing Torch, initializing CUDA or opening Isaac.
    result = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
                            text=True, capture_output=True, check=True)
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("NaVILA-Bench requires an RTX GPU for its native camera renderer")
    if runtime_profile not in {"isaac41", "isaac51"}:
        raise ValueError("Unknown NaVILA runtime profile")
    if runtime_profile == "isaac41" and any("blackwell" in line.lower() or re.search(r"RTX\s+50[56789]0", line) for line in lines):
        raise RuntimeError("Pinned NaVILA-Bench uses Isaac Sim 4.1, which does not support Blackwell GPUs. "
                           "An Isaac/runtime migration is required before simulation; no GPU launch was attempted.")
    version = importlib.metadata.version("isaacsim")
    expected = ["4", "1", "0"] if runtime_profile == "isaac41" else ["5", "1", "0"]
    if version.split(".")[:3] != expected:
        raise ValueError(f"Runtime profile {runtime_profile} requires Isaac Sim {'.'.join(expected)}")
    if runtime_profile == "isaac51" and version != "5.1.0.0":
        raise ValueError("Isaac 5.1 profile requires the pinned 5.1.0.0 package")
    return {"profile": runtime_profile, "isaacsim": version, "gpus": lines}


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
                self.simulation_context.clear_instance()
                if self.runtime["profile"] == "isaac51":
                    self.application.close(wait_for_replicator=False)
                else:
                    self.application.close()


def create_native(root, *, task, seed, runtime_profile="isaac41"):
    runtime = preflight(runtime_profile)
    prepared = None
    namespace = "omni.isaac.lab"
    lab_revision = ISAACLAB_REVISION
    if runtime_profile == "isaac51":
        compatibility = importlib.import_module(f"{__package__}.isaac51" if __package__ else "isaac51")
        root, prepared = compatibility.load_prepared(root)
        # Generated upstream imports use this sibling module without importing the
        # harness package into the native interpreter.
        sys.modules["isaac51"] = compatibility
        sys.path[:0] = [str(root / path) for path in (
            "isaaclab_exts/omni.isaac.vlnce", "isaaclab_exts/omni.isaac.matterport", "scripts/rsl_rl")]
        namespace, lab_revision = "isaaclab", compatibility.ISAACLAB_REVISION
    else:
        root = validate_source(root)
    episode, assets = load_episode(root, task)
    policy_path = root / POLICY
    policy_sha256 = hashlib.sha256(policy_path.read_bytes()).hexdigest()
    if policy_path.stat().st_size < 1024:
        raise ValueError("Go2 locomotion checkpoint is missing or remains a Git LFS pointer")
    import inspect
    AppLauncher = importlib.import_module(namespace + ".app").AppLauncher
    lab_path = Path(inspect.getfile(AppLauncher)).parent
    lab_root = subprocess.check_output(["git", "-C", str(lab_path), "rev-parse", "--show-toplevel"], text=True).strip()
    validate_source(lab_root, lab_revision)
    application = AppLauncher(headless=True, enable_cameras=True).app
    if runtime_profile == "isaac51":
        import faulthandler
        import signal
        # A native fault on a physics thread cannot be recovered by AppLauncher's
        # deferred Python signal callback. Preserve process failure for the owner.
        faulthandler.disable()
        signal.signal(signal.SIGSEGV, signal.SIG_DFL)
        signal.signal(signal.SIGABRT, signal.SIG_DFL)
        faulthandler.enable()
    simulation_context = importlib.import_module(namespace + ".sim").SimulationContext
    native = None
    try:
        import torch
        ManagerBasedRLEnv = importlib.import_module(namespace + ".envs").ManagerBasedRLEnv
        load_yaml = importlib.import_module(namespace + ".utils.io").load_yaml
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
        cfg.seed = seed
        if prepared is not None:
            cfg.scene.robot.spawn.usd_path = prepared["go2_usd"]
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
        agent = load_yaml(policy_path.parent / "params/agent.yaml")
        if agent["policy"]["history_length"] != 9:
            raise ValueError("Original Go2 policy history length changed")
        runner = OnPolicyRunner(wrapped, agent, log_dir=None, device=agent["device"])
        runner.load(str(policy_path))
        policy = runner.get_inference_policy(device=native.device)
        env = VLNEnvWrapper(wrapped, policy, TASK, episode, max_length=MAX_STEPS,
            high_level_obs_key="camera_obs",
            measure_names=["PathLength", "DistanceToGoal", "Success", "SPL", "OracleNavigationError", "OracleSuccess"])
        with torch.inference_mode():
            _, info = env.reset()
        # The published Go2 wrapper warms up for 100 control steps before resetting metrics.
        native.preserve_terminal = True
        result = NativeEpisode(env, application, episode, info, seed=seed, policy_sha256=policy_sha256)
        result.runtime = runtime
        result.simulation_context = simulation_context
        return result
    except BaseException:
        try:
            if native is not None:
                native.close()
        finally:
            simulation_context.clear_instance()
            if runtime_profile == "isaac51":
                application.close(wait_for_replicator=False)
            else:
                application.close()
        raise
