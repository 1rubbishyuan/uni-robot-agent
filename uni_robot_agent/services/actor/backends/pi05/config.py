"""Explicit training and action profiles for the supported Pi0.5 runtimes."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from uni_robot_agent.services.actor.contracts import ActorProfile
from uni_robot_agent.services.actor.profiles import x5_fields

CAMERAS = ("cam_high", "cam_left_wrist", "cam_right_wrist")
ROBODOJO_CONFIG = "pi05_base_aloha_full_sim_arx-x5_seed_0"


class Pi05Config(BaseModel):
    model_config = ConfigDict(extra="forbid")
    runtime: Literal["openpi_jax"]
    checkpoint: str
    profile: Literal["robodojo_pi05"]
    model_config_name: str
    checkpoint_sha256: str | None = None
    xpolicylab_root: str | None = None


def profile_for(name: str) -> ActorProfile:
    if name == "robodojo_pi05":
        return ActorProfile(
            name=name,
            cameras=list(CAMERAS),
            observation_fields=x5_fields(),
            # The pinned legacy binding clips finite model gripper predictions.
            action_fields=x5_fields(bounded_gripper=False),
            action_horizon=50,
            control_frequency_hz=25,
            image_convention="RGB HWC; pinned runtime resizes with padding to 224 and converts to CHW",
        )
    raise ValueError(f"Unknown Pi0.5 profile: {name}")
