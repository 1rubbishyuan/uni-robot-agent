"""Pinned native API boundary loaded by RoboUse's stdio worker host.

No simulator is imported until make(). A child owns one episode, its physics and
all camera contexts; no control loops or task graders are reimplemented here.
"""
import importlib
import json
from pathlib import Path
import sys
import numpy as np
from uni_robot_agent.integrations.robouse.environment.control import quaternion
from uni_robot_agent.integrations.robouse.environment.worker_frames import pack_frames


def array(value):
    return value.detach().cpu().numpy() if hasattr(value, "detach") else np.asarray(value)


def native_module(family):
    import robouse
    if family in {"maniskill_cpu", "robocasa"}:
        name = "maniskill" if family == "maniskill_cpu" else family
        return importlib.import_module(f"robouse.backends.{name}.worker")
    # GPU-track source uses sibling imports. Keep that private convention in the child.
    directory = Path(robouse.__file__).parent / "backends/gpu/worker"
    sys.path.insert(0, str(directory))
    name = {"maniskill_gpu": "maniskill", "genesis": "genesis", "factory": "isaaclab"}[family]
    return importlib.import_module(name + "_sim")


class Worker:
    def __init__(self):
        self.native = None
        self.camera_capture = None

    def make(self, family, spec, seed):
        from uni_robot_agent.integrations.robouse.tasks import check_version
        check_version()
        if self.native is not None:
            raise ValueError("One native episode per worker process")
        self.family = family
        if family in {"maniskill_cpu", "maniskill_gpu", "genesis"}:
            from importlib.metadata import version
            package, expected = ("genesis-world", "0.3.3") if family == "genesis" else ("mani-skill", "3.0.1")
            if version(package) != expected:
                raise ValueError(f"This native adapter requires {package}=={expected}")
        module = native_module(family)
        self.module = module
        if family == "maniskill_cpu":
            from robouse.backends.maniskill.backend import TASKS
            task = TASKS[spec.get("env") or spec["id"]]
            self.native = module.Worker()
            meta = self.native.make(env_id=task["env"], seed=seed, render_size=int(spec.get("render_size", 384)))
        elif family == "robocasa":
            from robouse.backends.robocasa.backend import TASKS, EPISODES
            key = spec.get("env") or spec["id"]
            task = TASKS[key]
            path = EPISODES / f"{key}.json"
            ep_meta = json.loads(path.read_text()) if path.exists() else None
            self.native = module.Worker()
            meta = self.native.make(env_name=task["env"], layout=task["layout"], style=task["style"],
                seed=int((ep_meta or {}).get("robouse_seed", task.get("seed", seed))),
                tool=task.get("tool", "down"), ep_meta=ep_meta)
        else:
            if family == "factory" and module.RTX:
                raise ValueError("Factory capture currently supports the native MuJoCo proxy renderer only; set ROBOUSE_IL_RTX=0")
            self.native = module.make(spec["env"], seed, spec.get("opts") or {})
            meta = self.native.info()
        if family == "factory":
            self.held_opening = float(np.clip(np.abs(array(self.native.u.joint_pos)[0, 7:9]).mean()/.04, 0, 1))
        return {"cameras": meta["cameras"], "family": family}

    def control_state(self):
        n, family = self.native, self.family
        extra = {}
        if family in {"maniskill_cpu", "maniskill_gpu"}:
            pose = n.u.agent.tcp.pose
            p, q = array(pose.p)[0], array(pose.q)[0]
            opening = np.mean(array(n.u.agent.robot.get_qpos())[0, -2:])/.04
            if family == "maniskill_cpu":
                extra = {"target_position": n.p_cmd.tolist(), "target_quaternion": quaternion(n.R_cmd)}
                drive = (n.grip_target+1)/2
            else:
                cfg = n.u.agent._controller_configs["pd_ee_delta_pose"]["arm"]
                if cfg.frame != "root_translation:root_aligned_body_rotation" or cfg.use_target or not cfg.normalize_action:
                    raise ValueError("Unsupported ManiSkill pose controller configuration")
                extra = {"base_quaternion": array(n.u.agent.robot.pose.q)[0].tolist(),
                         "rotation_scale": float(cfg.rot_lower)}
                drive = (n.grip+1)/2
        elif family == "robocasa":
            p = np.asarray(n.obs["robot0_eef_pos"])
            q = np.asarray(n.obs["robot0_eef_quat"])[[3,0,1,2]]
            opening = np.abs(n.obs["robot0_gripper_qpos"]).mean()/.04
            drive = (1-n.grip)/2
            extra = {"target_position": n.target_p.tolist(), "target_quaternion": quaternion(n.target_R)}
        elif family == "genesis":
            p, q = n._tcp()
            opening = np.abs(n._q()[7:9]).mean()/.04
            drive = (n.grip+1)/2
        else:
            u = n.u
            p, q = n._np(u.fingertip_midpoint_pos), n._np(u.fingertip_midpoint_quat)
            opening = np.abs(array(u.joint_pos)[0,7:9]).mean()/.04
            drive = self.held_opening
            extra = {"held_opening": self.held_opening, "clockwise_only": bool(u.cfg_task.unidirectional_rot),
                     "ema": float(u.ema_factor), "previous_action": n._np(u.actions).tolist(),
                     "position_scale": n._np(u.pos_threshold).tolist(), "rotation_scale": n._np(u.rot_threshold).tolist()}
        return {"position": list(map(float,p)), "quaternion": list(map(float,q)),
                "opening": float(np.clip(opening,0,1)), "drive_opening": float(np.clip(drive,0,1)), **extra}

    def step(self, action):
        result = self.native.step(action)
        return {"success": bool(result["success"])}

    def success(self):
        return {"success": bool(self.native.success()["success"])}

    def render(self, camera, width, height):
        if self.family in {"maniskill_cpu", "robocasa"}:
            return self.native.render(camera=camera, width=width, height=height)
        return np.ascontiguousarray(self.native._render(camera))

    def capture(self, cameras, depth):
        from uni_robot_agent.integrations.robouse.environment.worker_cameras import capture
        return pack_frames(capture(self, cameras, depth))

    def close(self):
        if self.camera_capture:
            self.camera_capture.close()
        if self.native is not None:
            if self.family in {"maniskill_cpu", "robocasa"}:
                self.native.env.close()
            else:
                self.native.close()
