"""Compact physical measurements for model-facing tool results."""

from copy import deepcopy
import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uni_robot_agent.tools.results import ResultFields


def round_physical(value):
    """Copy physical values with at most five decimal places and positive zero."""
    if isinstance(value, float):
        return round(value, 5) or 0.0
    if isinstance(value, dict):
        return {key: round_physical(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(round_physical(item) for item in value)
    return value


def state_for_model(state):
    """Keep provenance and timing intact while formatting measured robot state."""
    result = deepcopy(state)
    if result.get("kind") == "devices":
        result["devices"] = round_physical(result["devices"])
        return result
    for arm in result["arms"].values():
        for key in ("control_pose", "joints", "gripper", "gripper_command"):
            arm[key] = round_physical(arm[key])
        for key in ("gripper_opening_m", "gripper_target_opening_m", "gripper_applied_command",
                    "gripper_applied_target_opening_m", "T_world_from_root"):
            if key in arm["metadata"]:
                arm["metadata"][key] = round_physical(arm["metadata"][key])
    for key in ("policy_state", "eef_pos", "eef_quat_xyzw", "gripper_qpos", "actor_observation_state"):
        if key in result["metadata"]:
            result["metadata"][key] = round_physical(result["metadata"][key])
    for camera in result["metadata"].get("camera_provenance", {}).values():
        alignments = list(camera.get("actor_joint_alignment", {}).values())
        if "joint_alignment" in camera:
            alignments.append(camera["joint_alignment"])
        for aligned in alignments:
            aligned["joint_position_rad"] = round_physical(aligned["joint_position_rad"])
    return result


def receipt_summary(fields: "ResultFields", *, data: dict, images: tuple[Path, ...], call_id: str) -> str:
    """Format existing feedback only; never query, execute, print or infer success.

    Keep reported false/zero/null values and stopping evidence. Tool-specific
    payloads are listed by key, not replaced by a fabricated Direct result.
    """
    result = fields.model_dump(exclude_unset=True)
    result["call_id"] = call_id
    result["images"] = [str(path) for path in images]
    if "image_cameras" in data:
        result["image_cameras"] = data["image_cameras"]
    remaining = sorted(data.keys() - type(fields).model_fields.keys() - {"image_cameras"})
    if remaining:
        result["data_fields"] = remaining
    return json.dumps(result, ensure_ascii=False)
