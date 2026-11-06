"""Translate X5 observations and decoded absolute action dictionaries."""

from collections.abc import Mapping, Sequence

import numpy as np

from uni_robot_agent.services.actor.contracts import ActorObservation
from uni_robot_agent.services.actor.images import decode_image

from .config import XPolicyLabConfig


def observation_payload(observation: ActorObservation, config: XPolicyLabConfig) -> dict:
    if len(observation.state) != 14 or set(observation.images) != set(config.cameras.values()):
        raise ValueError("Observation does not match the configured X5 profile")
    values = np.asarray(observation.state, dtype=np.float32)
    if not np.isfinite(values).all() or not all(0 <= values[index] <= 1 for index in (6, 13)):
        raise ValueError("X5 state requires finite float32 joints and normalized grippers")
    state = {}
    for arm, offset in (("left", 0), ("right", 7)):
        state[f"{arm}_arm_joint_state"] = values[offset:offset + 6]
        state[f"{arm}_ee_joint_state"] = values[offset + 6:offset + 7]
    return {
        "data_format_version": "v1.0",
        "instruction": observation.instruction,
        "vision": {slot: {"color": decode_image(observation.images[camera])}
                   for slot, camera in config.cameras.items()},
        "state": state,
        "additional_info": {"frequency": config.control_frequency_hz},
    }


def action_matrix(actions: object, horizon: int) -> list[list[float]]:
    """Require normalized, absolute actions; never infer delta semantics or clip."""
    if not isinstance(actions, Sequence) or isinstance(actions, (str, bytes)) or len(actions) != horizon:
        raise ValueError(f"XPolicyLab must return exactly {horizon} action dictionaries")
    fields = {f"{arm}_{part}": size for arm in ("left", "right")
              for part, size in (("arm_joint_state", 6), ("ee_joint_state", 1))}
    result = []
    for action in actions:
        if not isinstance(action, Mapping) or set(action) != set(fields):
            raise ValueError("Action must contain only the four X5 joint/gripper fields")
        row = []
        for name, size in fields.items():
            value = np.asarray(action[name])
            if value.shape != (size,) or value.dtype.kind not in "fiu" or not np.isfinite(value).all():
                raise ValueError(f"{name} must be a finite numeric vector of length {size}")
            row.extend(value.astype(float).tolist())
        if not all(0 <= row[index] <= 1 for index in (6, 13)):
            raise ValueError("Upstream gripper values must use normalized opening in [0,1]")
        result.append(row)
    return result
