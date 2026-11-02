"""Versioned actor protocol; model libraries are deliberately absent here."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

PROTOCOL = "uni_robot_agent.actor.v1"


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Image(Contract):
    format: Literal["png"] = "png"
    data: str = Field(min_length=1, max_length=16 * 1024 * 1024)


class ActorProfile(Contract):
    name: str
    cameras: list[str]
    state_fields: list[str]
    action_fields: list[str]
    action_horizon: int = Field(gt=0)
    state_units: list[str]
    action_units: list[str]
    coordinate_frame: str
    gripper_convention: str
    image_convention: str

    @model_validator(mode="after")
    def dimensions(self):
        if len(self.state_fields) != len(self.state_units) or len(
            self.action_fields
        ) != len(self.action_units):
            raise ValueError("Profile fields and units must have matching dimensions")
        if not self.cameras or len(set(self.cameras)) != len(self.cameras):
            raise ValueError("Profile cameras must be nonempty and unique")
        return self


class ActorInfo(Contract):
    protocol: Literal["uni_robot_agent.actor.v1"] = PROTOCOL
    model: str
    runtime: str
    model_id: str
    checkpoint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_config_name: str
    profile: ActorProfile
    policy_rng_seed: int | None = None


class ActorObservation(Contract):
    instruction: str
    state: list[FiniteFloat] = Field(min_length=1, max_length=1024)
    images: dict[str, Image]


class CreateSession(Contract):
    protocol: Literal["uni_robot_agent.actor.v1"] = PROTOCOL
    model_id: str
    profile: str


class SessionInfo(Contract):
    session_id: str
    model_id: str
    next_sequence: int = Field(ge=0)
    status: Literal["ready", "running", "poisoned", "closing", "closed"]


class PredictRequest(Contract):
    session_id: str
    request_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    sequence: int = Field(ge=0)
    observation: ActorObservation


class PredictReply(Contract):
    session_id: str
    request_id: str
    sequence: int = Field(ge=0)
    model_id: str
    checkpoint_sha256: str
    profile: str
    actions: list[list[FiniteFloat]]


class Receipt(Contract):
    session_id: str
    request_id: str
    sequence: int = Field(ge=0)
    status: Literal["running", "succeeded", "failed"]
    reply: PredictReply | None = None
    error: str | None = None
