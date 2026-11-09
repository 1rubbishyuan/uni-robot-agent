"""Physical navigation semantics and isolated NaVILA runtime configuration."""
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from uni_robot_agent.services.actor.contracts import ActorField, HistoryRequirement, SymbolicActorProfile

REVISION = "76b98f233dd0fff05dfcd69435eec6740febff9d"
CHECKPOINT_REVISION = "b2294e96581454468d6b94f38201f4f965ef48b7"


def actor_profile():
    return SymbolicActorProfile(name="navila_8b_navigation", cameras=["front"],
        observation_fields=[], image_convention="RGB HWC; native JPEG and video preprocessing in backend",
        history={"front": HistoryRequirement(min_frames=1, max_frames=128, scope="episode")},
        symbols={
            "move_forward": [ActorField(name="distance_m", unit="m", semantics="forward_displacement",
                frame="body", minimum=.25, maximum=.75)],
            "turn": [ActorField(name="yaw_rad", unit="rad", semantics="relative_yaw",
                frame="body", minimum=-math.pi / 4, maximum=math.pi / 4)],
            "stop": [],
        })


class NaVILAConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    runtime: Literal["navila_tcp"] = "navila_tcp"
    model: Literal["navila-llama3-8b-8f"] = "navila-llama3-8b-8f"
    model_config_name: Literal["llama_3_8f"] = "llama_3_8f"
    upstream_host: str = Field(default="127.0.0.1", min_length=1)
    upstream_port: int = Field(default=54321, gt=0, le=65535)
    checkpoint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    exclusive_upstream: Literal[True]
    request_timeout_s: float = Field(default=180, gt=0, le=900)
    max_response_bytes: int = Field(default=65536, gt=0, le=1024 * 1024)

    @property
    def profile(self):
        return actor_profile()


def resolve_config(settings):
    return NaVILAConfig.model_validate(settings)


def service_limits(config):
    return {"max_batch_size": 1, "max_sessions": 1}
