"""Explicit physical-field mappings for the pinned native GR00T API."""
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from uni_robot_agent.services.actor.contracts import ActorProfile

GR00T_REVISION = "9b37aa1ce69c73c6d165233fa88128283bba4508"


class Gr00tConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    model: str = "gr00t_n1_6"
    runtime: Literal["gr00t_native"] = "gr00t_native"
    native_root: Path
    source_revision: Literal[GR00T_REVISION]
    checkpoint: Path
    checkpoint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_config_name: str = Field(min_length=1)
    embodiment_tag: str = Field(min_length=1)
    profile: ActorProfile
    camera_mapping: dict[str, str]
    observation_mapping: dict[str, list[str]]
    action_mapping: dict[str, list[str]]
    language_key: str = "annotation.human.task_description"
    device: str = "cuda:0"

    @model_validator(mode="after")
    def complete_mappings(self):
        cameras = list(self.camera_mapping.values())
        if (any(not key for key in self.camera_mapping) or len(set(cameras)) != len(cameras)
                or set(cameras) != set(self.profile.cameras)):
            raise ValueError("GR00T camera mapping must cover each profile camera exactly once")
        for mapping, fields in ((self.observation_mapping, self.profile.observation_fields),
                                (self.action_mapping, self.profile.action_fields)):
            names = [name for group in mapping.values() for name in group]
            if (any(not key or not group for key, group in mapping.items())
                    or len(names) != len(set(names)) or set(names) != {field.name for field in fields}):
                raise ValueError("GR00T modality mapping must cover each declared physical field exactly once")
        return self

    def identity(self):
        return self.model_dump(mode="json", exclude={"native_root", "checkpoint", "device"})


def resolve_config(settings):
    return Gr00tConfig.model_validate(settings)


def service_limits(config):
    return {"max_batch_size": 1, "max_sessions": 1}
