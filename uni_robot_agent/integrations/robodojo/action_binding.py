"""RoboDojo X5 observation, command and reviewer bindings.

This model binding owns its 14D dual-arm convention. Native FK is requested
through the explicit episode API; no simulator package is imported here.
"""
from __future__ import annotations

import copy
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

from uni_robot_agent.services.actor.backends.pi05.config import profile_for
from uni_robot_agent.services.actor.client import ActorClient
from uni_robot_agent.services.actor.contracts import ActorObservation
from uni_robot_agent.services.actor.images import encode_image
from uni_robot_agent.services.actor.profiles import validate_x5_joint_profile
from uni_robot_agent.services.environment.contracts import (
    ArmCommand,
    JointCommand,
    JointTarget,
    Pose,
    PoseCommand,
    PreviewRequest,
)

ARMS = ("left", "right")


def text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} requires nonempty text")


def vector(value, size, field):
    if (not isinstance(value, list) or len(value) != size
            or any(type(x) not in (int, float) for x in value)
            or not np.isfinite(value).all()):
        raise ValueError(f"{field} requires a finite {size}-vector")
    return np.asarray(value, dtype=float)


def validate_progress(progress):
    if not isinstance(progress, dict) or set(progress) != {
        "verified_completed", "currently_attempting", "remaining",
    }:
        raise ValueError("task_progress requires completed/current/remaining subgoals")
    text(progress["currently_attempting"], "currently_attempting")
    for key in ("verified_completed", "remaining"):
        if not isinstance(progress[key], list):
            raise TypeError(f"task_progress.{key} requires a list")
        for item in progress[key]:
            text(item, key)


def validate_pose(target):
    if not isinstance(target, dict) or not {
        "position", "quaternion_wxyz", "gripper_closed",
    } <= set(target) or set(target) - {
        "position", "quaternion_wxyz", "gripper_closed", "gripper_opening", "index",
    }:
        raise ValueError("Target requires position, quaternion_wxyz and gripper_closed")
    vector(target["position"], 3, "position")
    q = vector(target["quaternion_wxyz"], 4, "quaternion_wxyz")
    if abs(np.linalg.norm(q) - 1) > 1e-4:
        raise ValueError("quaternion_wxyz must be unit length")
    if type(target["gripper_closed"]) is not bool:
        raise ValueError("gripper_closed must be boolean")
    if "gripper_opening" in target:
        opening = target["gripper_opening"]
        if type(opening) not in (int, float) or not np.isfinite(opening) or not 0 <= opening <= 1:
            raise ValueError("gripper_opening must be finite and in 0..1")


def validate_eef_bound(target, measured):
    p = np.asarray(target["position"], float)
    q = np.asarray(target["quaternion_wxyz"], float)
    start = np.asarray(measured, float)
    angle = (Rotation.from_quat(q[[1, 2, 3, 0]]) *
             Rotation.from_quat(start[[4, 5, 6, 3]]).inv()).magnitude()
    if np.linalg.norm(p - start[:3]) > 0.05 + 1e-10 or angle > 0.35 + 1e-10:
        raise ValueError("EEF target exceeds 5 cm or 0.35 rad from measured grasp site")


def validate_decision(decision, context, actions_per_chunk):
    if not isinstance(decision, dict) or decision.get("mode") not in ("student", "edit", "eef", "stop"):
        raise ValueError("VLA decision mode must be student, edit, eef, or stop")
    mode = decision["mode"]
    required = {"request_id", "mode", "steps", "reason", "assessment", "continuation"}
    required |= {"edit"} if mode == "edit" else {"target"} if mode == "eef" else set()
    if set(decision) != required:
        raise ValueError(f"Decision fields must be {sorted(required)}")
    continuation = decision["continuation"]
    if continuation not in ("continue", "stop", "phase_complete", "failure"):
        raise ValueError("continuation must be continue, stop, phase_complete or failure")
    if decision["request_id"] != context["request_id"]:
        raise ValueError("Stale VLA request_id")
    text(decision["reason"], "reason")
    if mode == "stop":
        if type(decision["steps"]) is not int or decision["steps"] != 0 or continuation == "continue":
            raise ValueError("stop requires steps=0 and a stop/phase_complete/failure reason")
    else:
        limit = actions_per_chunk if mode == "student" else min(5, actions_per_chunk)
        if type(decision["steps"]) is not int or not 1 <= decision["steps"] <= limit:
            raise ValueError(f"{mode} steps must be in 1..{limit}")
        if continuation != "continue":
            raise ValueError("Use mode=stop with steps=0 to stop before execution")
    assessment = decision["assessment"]
    if not isinstance(assessment, dict):
        raise TypeError("Outcome and intent assessment required")
    validate_progress(assessment.get("task_progress"))
    for field in ("current_subgoal", "execution_evidence", "expected_next_intent",
                  "predicted_next_intent", "intent_evidence"):
        text(assessment.get(field), field)
    execution, intent = assessment.get("execution_status"), assessment.get("intent_status")
    if execution not in ("not_started", "progressing", "failed", "uncertain", "recovered"):
        raise ValueError("Unknown execution_status")
    if intent not in ("aligned", "misaligned", "uncertain"):
        raise ValueError("Unknown intent_status")
    if (context["step_id"] == 0) != (execution == "not_started"):
        raise ValueError("not_started is required only at episode step zero")
    if mode in ("edit", "eef") and execution != "failed" and intent != "misaligned":
        raise ValueError("Takeover requires observed failure or misaligned intent")
    if mode == "edit":
        edits = decision["edit"]
        if not isinstance(edits, dict) or set(edits) != set(ARMS):
            raise ValueError("Explicit left/right edits required")
        for edit in edits.values():
            if not isinstance(edit, dict) or set(edit) != {"delta_position", "delta_rotation_vector", "gripper"}:
                raise ValueError("Invalid edit fields")
            dp = vector(edit["delta_position"], 3, "delta_position")
            dr = vector(edit["delta_rotation_vector"], 3, "delta_rotation_vector")
            if np.linalg.norm(dp) > .05 + 1e-10 or np.linalg.norm(dr) > .35 + 1e-10:
                raise ValueError("Edit exceeds 5 cm or 0.35 rad")
            if edit["gripper"] not in ("keep", "open", "closed"):
                raise ValueError("gripper must be keep, open or closed")
    elif mode == "eef":
        targets = decision["target"]
        if not isinstance(targets, dict) or set(targets) != set(ARMS):
            raise ValueError("Explicit left/right targets required")
        for arm, target in targets.items():
            if not isinstance(target, dict) or set(target) != {"position", "quaternion_wxyz", "gripper_closed"}:
                raise ValueError("Invalid EEF target fields")
            validate_pose(target)
            validate_eef_bound(target, context["current_state"][arm]["grasp_pose_wxyz"])
    validated = copy.deepcopy(decision)
    validated["continuation"] = continuation
    return validated


def transform(position, quaternion_wxyz):
    q = np.asarray(quaternion_wxyz, float)
    t = np.eye(4)
    t[:3, :3] = Rotation.from_quat(q[[1, 2, 3, 0]]).as_matrix()
    t[:3, 3] = position
    return t


def pose(t):
    q = Rotation.from_matrix(t[:3, :3]).as_quat()
    return {"position": t[:3, 3].tolist(), "quaternion_wxyz": q[[3, 0, 1, 2]].tolist()}


def edited_targets(trajectory, steps, edit):
    """Smooth root-frame offset relative to the selected student prefix.

    Gripper overrides apply explicitly from the first step. Closure after an
    approach must be issued in a later decision using a fresh observation.
    """
    if type(steps) is not int or not 1 <= steps <= 15:
        raise ValueError("Execution prefix must be 1..15 steps")
    if not isinstance(edit, dict) or set(edit) != {
        "delta_position",
        "delta_rotation_vector",
        "gripper",
    }:
        raise ValueError("Edit requires delta_position, delta_rotation_vector, gripper")
    dp, dr = (
        np.asarray(edit["delta_position"], float),
        np.asarray(edit["delta_rotation_vector"], float),
    )
    if dp.shape != (3,) or dr.shape != (3,) or not np.isfinite([dp, dr]).all():
        raise ValueError("Finite 3D edit vectors required")
    if np.linalg.norm(dp) > 0.05 + 1e-10 or np.linalg.norm(dr) > 0.35 + 1e-10:
        raise ValueError("Edit exceeds 5 cm or 0.35 rad short-correction limit")
    if edit["gripper"] not in ("keep", "open", "closed"):
        raise ValueError("Unknown gripper override")
    targets = []
    for i, original in enumerate(trajectory[:steps]):
        t = transform(original["position"], original["quaternion_wxyz"])
        alpha = (i + 1) / steps
        t[:3, 3] += alpha * dp
        t[:3, :3] = Rotation.from_rotvec(alpha * dr).as_matrix() @ t[:3, :3]
        grip = (
            original["gripper_closed"]
            if edit["gripper"] == "keep"
            else edit["gripper"] == "closed"
        )
        targets.append(dict(index=i, **pose(t), gripper_closed=grip))
    return targets



def decision_targets(trajectory, decision, active_arms, current_state, gripper_targets):
    """Convert edit/eef decisions into explicit dual-arm EEF targets."""
    steps = decision["steps"]
    held = {}
    for arm in ("left", "right"):
        measured = current_state[arm]
        pose = measured["grasp_pose_wxyz"]
        opening = float(measured["gripper_command"])
        held[arm] = {
            "position": list(pose[:3]),
            "quaternion_wxyz": list(pose[3:]),
            "gripper_closed": opening < 0.5,
            "gripper_opening": opening,
        }
    if decision["mode"] == "eef":
        result = [{
            arm: copy.deepcopy(
                decision["target"][arm] if arm in active_arms else held[arm]
            )
            for arm in ("left", "right")
        } for _ in range(steps)]
        for row in result:
            for arm, opening in gripper_targets.items():
                row[arm]["gripper_opening"] = opening
                row[arm]["gripper_closed"] = opening < .5
        return result
    targets = {}
    for arm in ("left", "right"):
        if arm not in active_arms:
            targets[arm] = [copy.deepcopy(held[arm]) for _ in range(steps)]
            continue
        original = [row[arm] for row in trajectory]
        edited = edited_targets(original, steps, decision["edit"][arm])
        if decision["edit"][arm]["gripper"] == "keep":
            for target, row in zip(edited, original):
                target["gripper_opening"] = row["gripper_opening"]
        if arm in gripper_targets:
            for target in edited:
                target["gripper_opening"] = gripper_targets[arm]
                target["gripper_closed"] = gripper_targets[arm] < .5
        targets[arm] = edited
    return [
        {arm: targets[arm][index] for arm in ("left", "right")}
        for index in range(steps)
    ]


class ActionBinding:
    name = "robodojo_pi05"
    reviewer_prompt = Path(__file__).resolve().parents[2] / "resources/prompts/actor/robodojo_vla_decision.md"
    reviewer_supported = True
    default_max_chunks = 6

    def __init__(self, args, description, *, name="robodojo_pi05"):
        self.name = name
        if name not in {"robodojo_pi05", "robodojo_x5_joint"}:
            raise ValueError(f"Unsupported X5 action binding: {name}")
        if tuple(arm.name for arm in description.environment.arms) != ARMS:
            raise ValueError("RoboDojo actors require ordered left/right arm descriptors")
        if name == "robodojo_x5_joint" and (
            description.integration != "robodojo"
            or description.environment.action_binding != "robodojo_pi05"
            or description.environment.control_frequency_hz != 25
            or any(len(arm.joint_names) != 6 for arm in description.environment.arms)
        ):
            raise ValueError("robodojo_x5_joint requires the RoboDojo X5 environment at 25 Hz")
        checkpoint = getattr(args, "action_model_checkpoint_sha256", None)
        if not checkpoint:
            raise ValueError("RoboDojo actors require --action-model-checkpoint-sha256")
        expected = profile_for(name) if name == "robodojo_pi05" else name
        self.client = ActorClient(args.action_model_url, expected_profile=expected,
                                  checkpoint_sha256=checkpoint)
        try:
            info = self.client.info
            if name == "robodojo_pi05":
                if (info.model != "pi05" or info.runtime != "openpi_jax" or info.policy_rng_seed != 0
                        or info.model_config_name != "pi05_base_aloha_full_sim_arx-x5_seed_0"):
                    raise ValueError("RoboDojo requires the pinned Pi0.5 config and per-session seed-zero RNG")
            else:
                validate_x5_joint_profile(info.profile)
                if info.runtime != "xpolicylab_ws":
                    raise ValueError("robodojo_x5_joint requires the XPolicyLab bridge")
                available = {camera.name for camera in description.environment.cameras}
                if not set(info.profile.cameras) <= available:
                    raise ValueError("Environment lacks the actor's required cameras")
            self.model_name = info.model if name == "robodojo_x5_joint" else name
        except Exception:
            self.client.close()
            raise

    @property
    def action_horizon(self):
        return self.client.info.profile.action_horizon

    def prepare(self, episode, manifest, instruction, active_arms, gripper_targets):
        state = manifest["state"]
        current = {}
        for arm in ARMS:
            value = state["arms"][arm]
            current[arm] = {
                **value.get("metadata", {}), "joints": value["joints"], "gripper_command": value["gripper_command"],
                "grasp_pose_wxyz": [*value["control_pose"]["position"], *value["control_pose"]["quaternion"]],
                "grasp": {**value.get("metadata", {}).get("grasp", {}), "gripper": value["gripper"]},
            }
        images = episode.image_paths(manifest)
        camera_map = {"head": "cam_high", "left_wrist": "cam_left_wrist", "right_wrist": "cam_right_wrist"}
        model_images = ({camera_map.get(name, name): path for name, path in images.items()}
                        if self.name == "robodojo_pi05" else images)
        from PIL import Image
        encoded = {}
        for name in self.client.info.profile.cameras:
            with Image.open(model_images[name]) as image:
                encoded[name] = encode_image(np.asarray(image.convert("RGB")))
        observation = ActorObservation(
            state=[number for arm in ARMS for number in (*current[arm]["joints"], current[arm]["grasp"]["gripper"])],
            images=encoded, instruction=instruction)
        reply = self.client.predict(observation)
        raw_actions = np.asarray(reply.actions, dtype=np.float32)
        if raw_actions.shape != (self.action_horizon, 14) or not np.isfinite(raw_actions).all():
            raise ValueError(f"Actor must return finite {self.action_horizon}x14 actions")
        actions = raw_actions.copy()
        if self.name == "robodojo_pi05":
            actions[:, [6, 13]] = np.clip(actions[:, [6, 13]], 0, 1)
        elif not ((actions[:, [6, 13]] >= 0) & (actions[:, [6, 13]] <= 1)).all():
            raise ValueError("X5 actor grippers must be normalized to [0,1]")
        scoped = actions.copy()
        for arm, offset in (("left", 0), ("right", 7)):
            if arm not in active_arms:
                scoped[:, offset:offset + 7] = [*current[arm]["joints"], current[arm]["gripper_command"]]
            elif arm in gripper_targets:
                scoped[:, offset + 6] = gripper_targets[arm]
        commands = [JointCommand(arms={arm: JointTarget(joints=row[offset:offset + 6].tolist(), gripper=float(row[offset + 6]))
                    for arm, offset in (("left", 0), ("right", 7))}) for row in scoped]
        # The environment previews at most 50 steps; executed prefixes are capped at 40.
        preview = episode.env.preview(PreviewRequest(commands=commands[:50], expected_state_version=state["state_version"]))
        info = self.client.info
        return {"commands": commands, "preview": preview, "current_state": current,
                "images": images, "raw_actions": raw_actions, "scoped_actions": scoped,
                "metadata": {"inference_index": reply.sequence, "checkpoint_sha256": reply.checkpoint_sha256,
                             "model": info.model, "model_id": info.model_id, "runtime": info.runtime,
                             "model_config_name": info.model_config_name,
                             "profile": info.profile.model_dump(mode="json"),
                             "preview_horizon": min(len(commands), 50)}}

    def validate_decision(self, decision, context, actions_per_chunk):
        return validate_decision(decision, context, actions_per_chunk)

    def commands(self, proposal, decision, active_arms, gripper_targets):
        if decision["mode"] == "student":
            return proposal["commands"][:decision["steps"]]
        rows = decision_targets(proposal["preview"]["trajectory"], decision, active_arms,
                                proposal["current_state"], gripper_targets)
        return [PoseCommand(solver="dls", arms={arm: ArmCommand(
                    pose=Pose(position=target["position"], quaternion=target["quaternion_wxyz"]),
                    gripper=target.get("gripper_opening", 0.0 if target["gripper_closed"] else 1.0))
                if arm in active_arms else JointTarget(joints=proposal["current_state"][arm]["joints"],
                    gripper=proposal["current_state"][arm]["gripper_command"])
                for arm, target in row.items()}) for row in rows]

    def close(self):
        self.client.close()
