"""Explicit deployment declarations for the X5 XPolicyLab bridge."""

from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from uni_robot_agent.services.actor.profiles import x5_joint_profile

XPOLICYLAB_REVISION = "6baccd57ff958570d0db1588370289ee36c8bb70"


class XPolicyLabConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    upstream_url: str
    xpolicylab_root: Path
    upstream_revision: Literal[XPOLICYLAB_REVISION]
    model: str = Field(min_length=1)
    model_config_name: str = Field(min_length=1)
    checkpoint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    action_horizon: int = Field(gt=0, le=1024, strict=True)
    # Keys are upstream slots; values name the environment cameras.
    cameras: dict[str, str]
    action_semantics: Literal["absolute_joint_positions"]
    control_frequency_hz: Literal[25]
    gripper_convention: Literal["0=closed,1=open"]
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
        slots = {"cam_head", "cam_high", "cam_left_wrist", "cam_right_wrist"}
        if not self.cameras or not set(self.cameras) <= slots:
            raise ValueError("Declare supported XPolicyLab RGB camera slots")
        if len(set(self.cameras.values())) != len(self.cameras):
            raise ValueError("Each upstream camera must map to a distinct environment view")
        x5_joint_profile(list(self.cameras.values()), self.action_horizon)
        return self

    def identity(self) -> dict:
        """Record semantic deployment identity, excluding local paths and addresses."""
        return self.model_dump(mode="json", exclude={
            "upstream_url", "xpolicylab_root", "request_timeout_s",
        })
