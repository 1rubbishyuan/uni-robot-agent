"""Map declared actor fields to model wire vectors without robot assumptions."""

from collections.abc import Mapping, Sequence

import numpy as np

from uni_robot_agent.services.actor.contracts import ActorObservation, validate_vector
from uni_robot_agent.services.actor.images import decode_image

from .config import XPolicyLabConfig


def observation_payload(observation: ActorObservation, config: XPolicyLabConfig) -> dict:
    profile = config.profile
    if set(observation.images) != set(profile.cameras):
        raise ValueError("Observation cameras differ from the configured profile")
    validate_vector(observation.state, profile.observation_fields, "observation")
    values = np.asarray(observation.state, dtype=np.float32)
    if not np.isfinite(values).all():
        raise ValueError("Observation must be representable as finite float32 values")
    by_name = dict(zip((field.name for field in profile.observation_fields), values, strict=True))
    return {
        "data_format_version": "v1.0",
        "instruction": observation.instruction,
        "vision": {slot: {"color": decode_image(observation.images[camera])}
                   for slot, camera in config.camera_mapping.items()},
        "state": {key: np.asarray([by_name[name] for name in fields], dtype=np.float32)
                  for key, fields in config.observation_mapping.items()},
        "additional_info": {"frequency": profile.control_frequency_hz},
    }


def action_matrix(actions: object, config: XPolicyLabConfig) -> list[list[float]]:
    """Require declared output fields; never infer, clip or convert physical semantics."""
    profile = config.profile
    if (not isinstance(actions, Sequence) or isinstance(actions, (str, bytes))
            or len(actions) != profile.action_horizon):
        raise ValueError(f"XPolicyLab must return exactly {profile.action_horizon} action dictionaries")
    result = []
    for action in actions:
        if not isinstance(action, Mapping) or set(action) != set(config.action_mapping):
            raise ValueError("Action wire fields differ from the configured mapping")
        by_name = {}
        for key, fields in config.action_mapping.items():
            value = np.asarray(action[key])
            if (value.shape != (len(fields),) or value.dtype.kind not in "fiu"
                    or not np.isfinite(value).all()):
                raise ValueError(f"{key} must be a finite numeric vector of length {len(fields)}")
            by_name.update(zip(fields, value.astype(float).tolist(), strict=True))
        row = [by_name[field.name] for field in profile.action_fields]
        validate_vector(row, profile.action_fields, "action")
        result.append(row)
    return result
