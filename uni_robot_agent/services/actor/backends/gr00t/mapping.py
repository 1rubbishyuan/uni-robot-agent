"""Translate declared fields into native modalities without robot assumptions."""
from collections.abc import Mapping

import numpy as np

from uni_robot_agent.services.actor.contracts import validate_vector
from uni_robot_agent.services.actor.images import decode_image


def observation_payload(observation, config):
    profile = config.profile
    if set(observation.images) != set(profile.cameras):
        raise ValueError("GR00T observation cameras differ from its profile")
    validate_vector(observation.state, profile.observation_fields, "observation")
    by_name = dict(zip((field.name for field in profile.observation_fields), observation.state, strict=True))
    state = {key: np.asarray([[[by_name[name] for name in fields]]], dtype=np.float32)
             for key, fields in config.observation_mapping.items()}
    if any(not np.isfinite(value).all() for value in state.values()):
        raise ValueError("GR00T state must remain finite in float32")
    return {
        "video": {key: decode_image(observation.images[camera])[None, None]
                  for key, camera in config.camera_mapping.items()},
        "state": state,
        "language": {config.language_key: [[observation.instruction]]},
    }


def action_matrix(actions, config):
    """The official processor already decodes normalization into physical units."""
    if not isinstance(actions, Mapping) or set(actions) != set(config.action_mapping):
        raise ValueError("GR00T output modalities differ from the configured mapping")
    profile = config.profile
    columns = {}
    for key, fields in config.action_mapping.items():
        values = np.asarray(actions[key])
        if (values.shape != (1, profile.action_horizon, len(fields))
                or values.dtype.kind not in "fiu" or not np.isfinite(values).all()):
            raise ValueError(f"GR00T {key} must be a finite [1, horizon, fields] numeric array")
        columns.update((name, values[0, :, index]) for index, name in enumerate(fields))
    matrix = np.stack([columns[field.name] for field in profile.action_fields], axis=1).astype(float).tolist()
    for row in matrix:
        validate_vector(row, profile.action_fields, "action")
    return matrix
