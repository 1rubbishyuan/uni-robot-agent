"""Versioned actor protocol; model libraries are deliberately absent here."""

from typing import Literal
import math

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

PROTOCOL = "uni_robot_agent.actor.v2"


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Image(Contract):
    format: Literal["png"] = "png"
    data: str = Field(min_length=1, max_length=16 * 1024 * 1024)


class ActorField(Contract):
    """One ordered scalar with explicit physical meaning.

    Bindings validate semantic names and units, not only vector dimensions.
    A reference declares the baseline for a delta; absolute values use None.
    Frames and control points are independent of the model's wire field names.
    """

    name: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    semantics: str = Field(min_length=1)
    frame: str | None = Field(default=None, min_length=1)
    control_point: str | None = Field(default=None, min_length=1)
    reference: Literal["observation", "latest_ack", "previous_target"] | None = None
    minimum: FiniteFloat | None = None
    maximum: FiniteFloat | None = None

    @model_validator(mode="after")
    def bounds(self):
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("Field minimum cannot exceed its maximum")
        return self


class ActorProfile(Contract):
    name: str = Field(min_length=1)
    cameras: list[str]
    observation_fields: list[ActorField] = Field(max_length=1024)
    action_fields: list[ActorField] = Field(min_length=1, max_length=1024)
    action_horizon: int = Field(gt=0, le=1024, strict=True)
    control_frequency_hz: FiniteFloat = Field(gt=0)
    image_convention: str = Field(min_length=1)

    @model_validator(mode="after")
    def dimensions(self):
        if len(set(self.cameras)) != len(self.cameras) or any(not name for name in self.cameras):
            raise ValueError("Profile camera names must be nonempty and unique")
        if not self.cameras and not self.observation_fields:
            raise ValueError("Profile requires at least one observation field or camera")
        for fields in (self.observation_fields, self.action_fields):
            if len({field.name for field in fields}) != len(fields):
                raise ValueError("Profile field names must be unique within each vector")
        return self


def validate_vector(values: list[float], fields: list[ActorField], label: str) -> None:
    """Check declared scalar bounds without clipping or changing semantics."""
    if len(values) != len(fields):
        raise ValueError(f"{label} dimensions differ from the actor profile")
    for value, field in zip(values, fields, strict=True):
        if not math.isfinite(value):
            raise ValueError(f"{label}.{field.name} must be finite")
        if field.minimum is not None and value < field.minimum:
            raise ValueError(f"{label}.{field.name} is below its declared minimum")
        if field.maximum is not None and value > field.maximum:
            raise ValueError(f"{label}.{field.name} is above its declared maximum")


class ActorInfo(Contract):
    protocol: Literal["uni_robot_agent.actor.v2"] = PROTOCOL
    model: str
    runtime: str
    model_id: str
    checkpoint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_config_name: str
    profile: ActorProfile
    policy_rng_seed: int | None = None


class ActorObservation(Contract):
    instruction: str
    state: list[FiniteFloat] = Field(max_length=1024)
    images: dict[str, Image]


class CreateSession(Contract):
    protocol: Literal["uni_robot_agent.actor.v2"] = PROTOCOL
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
