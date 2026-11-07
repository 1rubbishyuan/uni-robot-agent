"""Explicit model wire mappings for the pinned XPolicyLab transport."""

from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from uni_robot_agent.services.actor.contracts import ActorProfile

XPOLICYLAB_REVISION = "6baccd57ff958570d0db1588370289ee36c8bb70"


class XPolicyLabConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    upstream_url: str
    xpolicylab_root: Path
    upstream_revision: Literal[XPOLICYLAB_REVISION]
    model: str = Field(min_length=1)
    model_config_name: str = Field(min_length=1)
    checkpoint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    profile: ActorProfile
    # Wire keys select ordered scalar fields, independently for input and output.
    camera_mapping: dict[str, str]
    observation_mapping: dict[str, list[str]]
    action_mapping: dict[str, list[str]]
    language_conditioned: Literal[True]
    fresh_observation_predictions: Literal[True]
    exclusive_upstream: Literal[True]
    request_timeout_s: float = Field(default=120, gt=0, le=300)

    @model_validator(mode="after")
    def validate_deployment(self):
        address = urlsplit(self.upstream_url)
        if (address.scheme not in {"ws", "wss"} or not address.hostname
                or address.username or address.password or address.query or address.fragment):
            raise ValueError("upstream_url must be a credential-free WS(S) URL")
        if any(not key for key in self.camera_mapping):
            raise ValueError("Camera wire keys must be nonempty")
        cameras = list(self.camera_mapping.values())
        if len(set(cameras)) != len(cameras) or set(cameras) != set(self.profile.cameras):
            raise ValueError("Camera mapping must cover each profile camera exactly once")
        for mapping, fields in ((self.observation_mapping, self.profile.observation_fields),
                                (self.action_mapping, self.profile.action_fields)):
            names = [name for group in mapping.values() for name in group]
            if any(not key or not group for key, group in mapping.items()):
                raise ValueError("Wire mappings require nonempty keys and field groups")
            if len(set(names)) != len(names) or set(names) != {field.name for field in fields}:
                raise ValueError("Wire mapping must cover each profile field exactly once")
        return self

    def identity(self) -> dict:
        """Record semantic deployment identity, excluding local paths and addresses."""
        return self.model_dump(mode="json", exclude={
            "upstream_url", "xpolicylab_root", "request_timeout_s",
        })
