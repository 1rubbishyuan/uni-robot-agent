"""Explicit training and action profiles for the supported Pi0.5 runtimes."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from uni_robot_agent.services.actor.contracts import ActorProfile

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
        fields = [
            f"{arm}_joint_{index}" for arm in ("left", "right") for index in range(6)
        ]
        fields = fields[:6] + ["left_gripper"] + fields[6:] + ["right_gripper"]
        return ActorProfile(
            name=name,
            cameras=list(CAMERAS),
            state_fields=fields,
            action_fields=fields,
            action_horizon=50,
            state_units=(["rad"] * 6 + ["normalized"]) * 2,
            action_units=(["rad"] * 6 + ["normalized"]) * 2,
            coordinate_frame="arx_x5_joint",
            gripper_convention="0=closed,1=open; binding clips predictions to [0,1]",
            image_convention="RGB HWC; pinned runtime resizes with padding to 224 and converts to CHW",
        )
    raise ValueError(f"Unknown Pi0.5 profile: {name}")
