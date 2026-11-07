"""RoboDojo native primitives; shared tools own loops and artifact encoding."""

from __future__ import annotations

import hashlib
import math
import time
from pathlib import Path
from typing import Any

import numpy as np

from uni_robot_agent.integrations.robodojo.environment.geometry import (
    CAMERA_ALIASES,
    capture_geometry,
)
from uni_robot_agent.integrations.robodojo.environment.kinematics import (
    GRASP_SITE,
    GRASP_SITE_DESCRIPTION,
    T_NATIVE_FROM_GRASP,
    DualKinematics,
    grasp_pose,
    native_pose,
    native_opening,
    physical_opening,
    transform,
)
from uni_robot_agent.integrations.robodojo.environment.native_compat import native_call, plan_eef_step
from uni_robot_agent.services.environment.contracts import (
    GoalRegionRequest,
    GoalRegionReply,
    ActionCommand,
    ArmCommand,
    ArmDescription,
    ArmControlCapabilities,
    ArmState,
    CameraDescription,
    EnvironmentDescription,
    EnvironmentState,
    JointCommand,
    JointTarget,
    NativeCommand,
    Pose,
    PoseCommand,
    RequestRejected,
    StepReceipt,
)
from uni_robot_agent.services.environment.snapshots import CameraFrame, CapturedObservation


def _quaternion_distance(left, right) -> float:
    """Return the shortest angular distance without depending on tool math."""
    left = np.asarray(left, dtype=float)
    right = np.asarray(right, dtype=float)
    left /= np.linalg.norm(left)
    right /= np.linalg.norm(right)
    return 2.0 * math.acos(float(np.clip(abs(np.dot(left, right)), 0.0, 1.0)))


class Adapter:
    """Expose one room's state and primitive commands through a native-call callback.

    The host controls thread placement through ``native_call``. Multi-step
    motion programs remain in the shared tools.
    """

    def __init__(self, env, output, layout, layout_sha256, *, episode_id="episode", slot=0,
                 ik_joint_delta_guard=True, depth_enabled=True, calibration_enabled=True,
                 native_call=native_call, solver_factory=None):
        if depth_enabled and not calibration_enabled:
            raise ValueError("Disabling camera calibration requires depth_enabled=False")
        self._native_call, self._solver_factory = native_call, solver_factory
        self.env, self.slot = env, slot
        self.output, self.layout = Path(output), Path(layout)
        if hashlib.sha256(self.layout.read_bytes()).hexdigest() != layout_sha256:
            raise ValueError("Layout SHA256 mismatch")
        self.layout_sha256, self.episode_id = layout_sha256, episode_id
        self.ik_joint_delta_guard = ik_joint_delta_guard
        self.depth_enabled = depth_enabled
        self.calibration_enabled = calibration_enabled
        self.started = self.sealed = self.closed = False
        self.stop_reason = self.finish_request = None
        self.physics_per_tick = round(1 / (25 * env.dt))
        if not np.isclose(self.physics_per_tick * env.dt, 1 / 25):
            raise ValueError("Native timestep is incompatible with 25 Hz control")
        self.robots = {robot.arm_name.removesuffix("_arm"): robot
                       for robot in env.robot_manager.robot_list if robot.type == "target"}
        if set(self.robots) != {"left", "right"} or any(
            robot.robot_name != "x5" for robot in self.robots.values()
        ):
            raise ValueError("RoboDojo requires its two target X5 arms")
        self.description = EnvironmentDescription(
            arms=[ArmDescription(
                name=name, control_frame=GRASP_SITE,
                grasp_site_description=GRASP_SITE_DESCRIPTION,
                rotation_radius_m=float(np.linalg.norm(T_NATIVE_FROM_GRASP[:3, 3])),
                joint_names=list(self.robots[name].arm_joints_name), joint_tracking_tolerance_rad=.35,
                control=ArmControlCapabilities(commands=["joints", "pose"], pose_solver="dls", joint_preview=True),
            ) for name in ("left", "right")],
            cameras=[CameraDescription(name=name, role="scene" if name == "head" else "wrist",
                                       has_depth=depth_enabled, has_calibration=calibration_enabled)
                     for name in CAMERA_ALIASES],
            control_frequency_hz=25, max_episode_steps=int(env.step_lim), action_binding="robodojo_pi05",
            metadata={"ik_joint_delta_guard": ik_joint_delta_guard,
                      "layout_sha256": layout_sha256, "physics_per_tick": self.physics_per_tick,
                      "workspace_bounds": [[-1., -.8, .5], [1., .8, 1.3]]},
        )
        self.fk = None
        self.gripper_commands: dict[str, float] = {}
        self.ik_solvers = {}

    @property
    def steps(self):
        return int(self.env.take_action_cnt[self.slot])

    @property
    def ended(self):
        return bool(self.env.end_flag[self.slot])

    def start(self, *, reset: bool = True) -> EnvironmentState:
        if self.started:
            raise RequestRejected("Native initialization was already attempted")
        self.started = True
        if reset:
            self.env.seed_manager.seed_info = {self.slot: {"scene_layout": str(self.layout)}}
            self.env.reset(seed=[0])
            # Match native evaluation: rewards and supporting arms follow reset.
            self.env.run_reward()
            if hasattr(self.env, "get_score"):
                self.env.get_score()
            if getattr(self.env, "interact", False) and hasattr(self.env, "query_support_arm_traj"):
                self.env.query_support_arm_traj(env_idx=self.slot)
        self.gripper_limits = self._gripper_limits()
        self.fk = DualKinematics(self.env, slot=self.slot)
        self.fk.check()
        self._initialize_robot_geometry()
        native = self.env.get_obs()["state"]
        self.gripper_commands = {name: float(native[f"{name}_ee_joint_state"][0]) for name in self.robots}
        from .ik.curobo import acquire_solvers
        self.ik_solvers = self._native_call(self.env, self._solver_factory or acquire_solvers)
        self.description.metadata["goal_region_search"] = True
        return self.read_state()

    def _initialize_robot_geometry(self):
        from .robot_geometry import load_robot_geometry
        from .gripper_geometry import X5Gripper

        models, arms, self.gripper_geometry = {}, {}, {}
        for name, robot in self.robots.items():
            key = str(Path(robot.urdf_path).resolve())
            if key not in models:
                models[key] = load_robot_geometry(robot.urdf_path)
            geometry = models[key]
            limits = self._robot_key(robot).data.joint_pos_limits[
                self.slot, robot.gripper_joint_indices].cpu().numpy()
            gripper = X5Gripper(geometry, scale=robot.gripper_scale, limits=limits,
                                mimic=robot.gripper_move["mimic"], sign=robot.gripper_move["sign"])
            self.gripper_geometry[name] = gripper
            model_id = geometry["asset_sha256"]["X5A.urdf"]
            arms[name] = {"model_id": model_id, "hand": gripper.summary}
        self.description.metadata.update({
            "gripper_opening_models": {name: g.model for name, g in self.gripper_geometry.items()},
            "robot_manual": "robodojo/robot-reference.md",
            "artifact_payloads": {"robot_calibration": {
                "schema_version": 1, "arms": arms,
                "models": {g["asset_sha256"]["X5A.urdf"]: g for g in models.values()},
            }},
        })

    def _robot_key(self, robot):
        manager = self.env.robot_manager
        return manager.robot_key[manager.robot_list.index(robot)]

    def _gripper_limits(self):
        limits = {}
        for arm, robot in self.robots.items():
            pair = self._robot_key(robot).data.joint_pos_limits[
                self.slot, robot.gripper_joint_indices[0]
            ].cpu().numpy()
            if not np.isfinite(pair).all() or pair[1] <= pair[0]:
                raise ValueError("Invalid physical gripper joint limits")
            limits[arm] = pair.tolist()
        return limits

    def _joint_limits(self, arm):
        robot = self.robots[arm]
        return self._robot_key(robot).data.soft_joint_pos_limits[
            self.slot, robot.arm_joint_indices
        ].cpu().numpy()

    def _active(self):
        if not self.started or self.ended or self.sealed:
            raise RequestRejected("Native episode is not active")

    def read_state(self) -> EnvironmentState:
        if not self.started:
            raise RequestRejected("Native episode has not started")
        manager, raw = self.env.robot_manager, self.env.get_obs()
        native, arms = raw["state"], {}
        for name, robot in self.robots.items():
            link_pose = manager.get_real_endpose(robot, is_relative=True)[self.slot]
            opening = physical_opening(
                robot, self.gripper_limits[name], manager.get_end_effector_real_val(robot)[self.slot][0],
            )
            control = grasp_pose(link_pose)
            gripper = self.gripper_geometry[name]
            bodies = {}
            for link in ("link6", "link7", "link8"):
                link_state = manager.get_link_pose(robot, link, env_idx_list=[self.slot], is_relative=True)[self.slot]
                bodies[link] = transform(link_state[:3], link_state[3:])
            applied = float(native[f"{name}_ee_joint_state"][0])
            target = self.gripper_commands[name]
            target_opening = gripper.target_opening(target)
            arms[name] = ArmState(
                control_pose=Pose(position=control["position"], quaternion=control["quaternion_wxyz"]),
                joints=np.asarray(manager.get_joint(robot)[self.slot], dtype=float).tolist(), gripper=opening,
                gripper_command=target,
                metadata={"gripper_opening_m": gripper.measure(bodies),
                          "gripper_target_opening_m": target_opening,
                          "gripper_applied_command": applied,
                          "gripper_applied_target_opening_m": gripper.target_opening(applied),
                          "T_world_from_root": self.fk.root(name).tolist()},
            )
        return EnvironmentState(
            arms=arms, state_version=self.steps, native_steps=self.steps, ended=self.ended,
            metadata={"instruction": raw["instruction"],
                      "remaining_steps": max(0, int(self.env.step_lim) - self.steps)},
        )

    def _complete(self, command):
        if set(command.arms) != set(self.robots):
            raise RequestRejected("A control step must contain both complete arms")

    def _check_joint_target(self, arm, target):
        values, limits = np.asarray(target.joints, dtype=float), self._joint_limits(arm)
        if values.shape != (len(limits),) or not np.isfinite(values).all():
            raise RequestRejected(f"Invalid {arm} joint target")
        if np.any(values < limits[:, 0] - 1e-5) or np.any(values > limits[:, 1] + 1e-5):
            raise RequestRejected(f"{arm} joint target exceeds limits")

    def validate_commands(self, commands: list[ActionCommand], mode: str) -> None:
        self._active()
        initial = self.read_state()
        previous = {name: np.asarray(arm.joints) for name, arm in initial.arms.items()}
        for command in commands:
            if isinstance(command, NativeCommand):
                raise RequestRejected("RoboDojo does not accept opaque native actions")
            self._complete(command)
            for name, target in command.arms.items():
                if isinstance(target, JointTarget):
                    self._check_joint_target(name, target)
                    values = np.asarray(target.joints)
                    if np.max(np.abs(values - previous[name])) > .25:
                        raise RequestRejected(f"{name} action prefix has a joint jump over 0.25 rad")
                    previous[name] = values
                elif mode == "eef":
                    p = initial.arms[name].control_pose
                    measured = [*p.position, *p.quaternion]
                    displacement = np.linalg.norm(np.asarray(target.pose.position) - measured[:3])
                    rotation = _quaternion_distance(target.pose.quaternion, measured[3:])
                    if displacement > .05 + 1e-10 or rotation > .35 + 1e-10:
                        raise RequestRejected("EEF target exceeds 5 cm or 0.35 rad from measured grasp site")

    def _step(self, commands):
        action, manager = {}, self.env.robot_manager
        for name, command in commands.items():
            robot = self.robots[name]
            action[manager.process_name(robot.arm_name)] = command["joints"]
            action[manager.process_name(robot.gripper_name)] = [
                native_opening(robot, self.gripper_limits[name], command["gripper"]),
            ]
        # Preserve native interpolation, supporting arms, rewards, checks, and pool batching.
        self.env.take_action(action)
        # Commit only after native execution: queued and rejected requests have no effect.
        # Native slew limiting reports an intermediate target, not the intent to retain.
        self.gripper_commands = {name: float(command["gripper"]) for name, command in commands.items()}
        self.env.get_obs_batch(env_idx_list=[self.slot], last_frame=True)

    def _recovery(self, arm):
        from .ik.recovery import Recovery
        return Recovery(self.ik_solvers[arm], self.fk.native_fk[arm], self._joint_limits(arm),
                        self.fk.root(arm), self.robots[arm].entity_origin_pose,
                        guard=self.ik_joint_delta_guard)

    def _solve_ik(self, arm, target, current):
        recovery = self._recovery(arm)
        return self._native_call(self.env, lambda _: recovery.solve(target, current))

    def solve_goal_region(self, request: GoalRegionRequest) -> GoalRegionReply:
        from .ik.goal_region import search
        self._active()
        state = self.read_state()
        if request.expected_state_version != state.state_version:
            raise RequestRejected("Goal-region observation is stale")
        if request.arm not in self.robots:
            raise RequestRejected("Unknown arm")
        recovery = self._recovery(request.arm)
        result = self._native_call(self.env, lambda _: search(
            recovery, self.fk.fk[request.arm], state.arms[request.arm].joints, request.goal,
            request.position_tolerance_m, request.orientation_tolerance_rad,
        ))
        return GoalRegionReply(state_version=state.state_version, **result)

    def step_pose(self, command: PoseCommand) -> StepReceipt:
        self._active()
        self._complete(command)
        holds = {name: target for name, target in command.arms.items() if isinstance(target, JointTarget)}
        for name, target in holds.items():
            self._check_joint_target(name, target)
        if command.solver == "dls":
            targets = {
                name: {"position": list(target.pose.position), "quaternion_wxyz": list(target.pose.quaternion),
                       "gripper_opening": target.gripper, "gripper_closed": target.gripper < .5}
                for name, target in command.arms.items() if isinstance(target, ArmCommand)
            }
            try:
                solved = self.fk.target(targets, joint_holds={name: target.model_dump() for name, target in holds.items()})
            except ValueError as error:
                return StepReceipt(accepted=False, state=self.read_state(), diagnostics={
                    "reason": "bounded_ik_rejected", "solver": "dls", "message": str(error),
                })
            values = solved["action"]
            commands = {name: {"joints": values[offset:offset + 6], "gripper": values[offset + 6]}
                        for name, offset in (("left", 0), ("right", 7))}
            diagnostic = {"arms": solved["diagnostics"], "solver": "dls"}
        else:
            action = {}
            for name, target in command.arms.items():
                if isinstance(target, ArmCommand):
                    action[f"{name}_ee_pose"] = native_pose(target.pose.position, target.pose.quaternion)
                    action[f"{name}_ee_joint_state"] = [target.gripper]
            plan = plan_eef_step(
                self.env, self.robots, action, ik_joint_delta_guard=self.ik_joint_delta_guard,
                slot=self.slot, joint_holds={name: target.model_dump() for name, target in holds.items()},
                solve=self._solve_ik,
            )
            if not plan["accepted"]:
                return StepReceipt(accepted=False, state=self.read_state(), diagnostics=plan)
            commands, diagnostic = plan.pop("commands"), plan
        self._step(commands)
        diagnostic["commanded_joints"] = {name: value["joints"] for name, value in commands.items()}
        return StepReceipt(accepted=True, state=self.read_state(), diagnostics=diagnostic)

    def step_joints(self, command: JointCommand) -> StepReceipt:
        self._active()
        self._complete(command)
        for name, target in command.arms.items():
            self._check_joint_target(name, target)
        self._step({name: target.model_dump() for name, target in command.arms.items()})
        return StepReceipt(accepted=True, state=self.read_state(), diagnostics={
            "commanded_joints": {name: target.joints for name, target in command.arms.items()},
        })

    def step_native(self, command: NativeCommand) -> StepReceipt:
        raise RequestRejected("RoboDojo does not accept opaque native actions")

    def preview_joints(self, commands: list[JointCommand]) -> dict[str, Any]:
        self._active()
        actions = []
        for command in commands:
            self._complete(command)
            row = []
            for name in ("left", "right"):
                target = command.arms[name]
                if len(target.joints) != 6:
                    raise RequestRejected("X5 preview requires six joints per arm")
                row.extend([*target.joints, target.gripper])
            actions.append(row)
        return self.fk.preview(actions)

    def capture(self, cameras: list[str] | None = None, *, high_resolution: bool = False) -> CapturedObservation:
        if not self.started:
            raise RequestRejected("Native episode has not started")
        selected = list(CAMERA_ALIASES) if cameras is None else cameras
        if not selected or len(set(selected)) != len(selected) or not set(selected) <= CAMERA_ALIASES.keys():
            raise RequestRejected("cameras must name distinct supported views")
        if high_resolution:
            frames = {name: CameraFrame(value["rgb"], value["depth"], value["intrinsic"],
                                        value["extrinsic"], value["calibration"])
                      for name, value in capture_geometry(self.env, slot=self.slot, cameras=selected,
                                                          depth_enabled=self.depth_enabled,
                                                          calibration_enabled=self.calibration_enabled,
                                                          native_call=self._native_call).items()}
        else:
            raw = self.env.get_obs_batch(env_idx_list=[self.slot], last_frame=True)[0]
            frames = {}
            for name in selected:
                source = next((alias for alias in CAMERA_ALIASES[name] if alias in raw["vision"]), None)
                if source is None:
                    raise RequestRejected(f"Camera {name!r} is absent")
                rgb = np.asarray(raw["vision"][source]["color"], dtype=np.uint8)[..., :3].copy()
                # Do not mutate the tiled render graph just to annotate an RGB record.
                frames[name] = CameraFrame(rgb, calibration={
                    "status": "unavailable" if self.calibration_enabled else "disabled",
                    "reason": ("Use capture_scene for same-frame projection calibration"
                               if self.calibration_enabled else "Camera calibration is disabled for this episode")})
        state = self.read_state()
        truncated = self.ended and self.steps >= int(self.env.step_lim) and not self.env.success[self.slot]
        return CapturedObservation(
            self.episode_id, state.state_version, time.time(), state.model_dump(mode="json"), frames,
            terminated=bool(self.ended and not truncated), truncated=bool(truncated),
        ).freeze()

    def outcome(self):
        complete = self.ended
        truncated = complete and self.steps >= self.env.step_lim and not self.env.success[self.slot]
        score = 1.0 if complete and self.env.success[self.slot] else (
            float(self.env.reward_manager.get_score()[self.slot]) / 100 if hasattr(self.env, "get_score") else 0.0
        )
        return {
            "task_success": bool(self.env.success[self.slot]) if complete else None,
            "native_score": score, "complete": complete,
            "terminated": bool(complete and not truncated), "truncated": bool(truncated),
            "steps": self.steps, "step_limit": int(self.env.step_lim), "finish_request": self.finish_request,
            "stop_reason": self.stop_reason or ("native_terminal" if complete else None),
        }

    def finish(self, reason: str, status: str | None = None) -> dict[str, Any]:
        if not self.sealed:
            self.sealed, self.stop_reason = True, reason
            self.finish_request = {"reason": reason, "status": status}
            final_check = status is not None or reason in {"give_up", "max_turns", "wall_time_limit"}
            if self.started and not self.ended and final_check:
                # Self-reported completion never substitutes for the official final check.
                score = float(self.env.reward_manager.get_reward(final_check=True)[self.slot])
                self.env.success[self.slot] = score > 1 - 1e-3
                self.env.end_flag[self.slot] = True
            self.env.save_video(self.slot, str(self.output / "videos" / "episode.mp4"), reason)
        return self.outcome()

    def close(self) -> None:
        for solver in self.ik_solvers.values():
            self._native_call(self.env, lambda _: solver.release(), allow_released=True)
        self.ik_solvers.clear()
        if not self.closed:
            self.closed = True
            self.env.close()
