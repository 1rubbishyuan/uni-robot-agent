"""Versioned actor protocol; model libraries are deliberately absent here."""

from typing import Annotated, Any, Literal
import math

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

PROTOCOL = "uni_robot_agent.actor.v3"


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


class HistoryRequirement(Contract):
    min_frames: int = Field(default=1, ge=1, le=4096)
    max_frames: int = Field(default=128, ge=1, le=4096)
    scope: Literal["episode", "recent"] = "episode"

    @model_validator(mode="after")
    def bounds(self):
        if self.min_frames > self.max_frames:
            raise ValueError("History minimum exceeds its maximum")
        return self


class HistoryFrame(Contract):
    frame_id: str = Field(min_length=1)
    captured_at: FiniteFloat = Field(ge=0)
    simulation_time_s: FiniteFloat = Field(ge=0)
    state_version: int = Field(ge=0)
    image: Image


class ActorProfile(Contract):
    kind: Literal["numeric"] = "numeric"
    name: str = Field(min_length=1)
    cameras: list[str]
    observation_fields: list[ActorField] = Field(max_length=1024)
    action_fields: list[ActorField] = Field(min_length=1, max_length=1024)
    action_horizon: int = Field(gt=0, le=1024, strict=True)
    control_frequency_hz: FiniteFloat = Field(gt=0)
    image_convention: str = Field(min_length=1)
    history: dict[str, HistoryRequirement] = Field(default_factory=dict)

    @model_validator(mode="after")
    def dimensions(self):
        if len(set(self.cameras)) != len(self.cameras) or any(not name for name in self.cameras):
            raise ValueError("Profile camera names must be nonempty and unique")
        if not set(self.history) <= set(self.cameras):
            raise ValueError("History must name declared cameras")
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


class SymbolicActorProfile(Contract):
    kind: Literal["symbolic"] = "symbolic"
    name: str = Field(min_length=1)
    cameras: list[str]
    observation_fields: list[ActorField] = Field(default_factory=list, max_length=1024)
    image_convention: str = Field(min_length=1)
    history: dict[str, HistoryRequirement] = Field(default_factory=dict)
    symbols: dict[str, list[ActorField]] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def declarations(self):
        if not self.cameras and not self.observation_fields:
            raise ValueError("Profile requires observations")
        if any(not name for name in self.cameras) or len(set(self.cameras)) != len(self.cameras):
            raise ValueError("Camera names must be nonempty and unique")
        if not set(self.history) <= set(self.cameras):
            raise ValueError("History must name declared cameras")
        if any(not label for label in self.symbols):
            raise ValueError("Prediction labels must be nonempty")
        for fields in (self.observation_fields, *self.symbols.values()):
            if len(fields) > 1024 or len({field.name for field in fields}) != len(fields):
                raise ValueError("Profile fields must be bounded and unique")
        return self


class NumericChunk(Contract):
    kind: Literal["numeric"] = "numeric"
    actions: list[list[FiniteFloat]] = Field(min_length=1, max_length=1024)
    evidence: dict[str, Any] = Field(default_factory=dict)


class SymbolicPrediction(Contract):
    kind: Literal["symbolic"] = "symbolic"
    label: str = Field(min_length=1)
    parameters: dict[str, FiniteFloat] = Field(default_factory=dict, max_length=1024)
    evidence: dict[str, Any] = Field(default_factory=dict)


Prediction = Annotated[NumericChunk | SymbolicPrediction, Field(discriminator="kind")]


def validate_prediction(prediction, profile) -> None:
    if isinstance(profile, ActorProfile) and isinstance(prediction, NumericChunk):
        if len(prediction.actions) != profile.action_horizon:
            raise ValueError("Prediction horizon differs from the declared profile")
        for row in prediction.actions:
            validate_vector(row, profile.action_fields, "action")
    elif isinstance(profile, SymbolicActorProfile) and isinstance(prediction, SymbolicPrediction):
        if prediction.label not in profile.symbols:
            raise ValueError("Prediction label is not declared by the profile")
        fields = profile.symbols[prediction.label]
        if set(prediction.parameters) != {field.name for field in fields}:
            raise ValueError("Prediction parameters differ from the declared label")
        validate_vector([prediction.parameters[field.name] for field in fields], fields, "prediction")
    else:
        raise ValueError("Prediction type differs from the actor profile")


def validate_observation(observation, profile) -> None:
    if set(observation.images) != set(profile.cameras):
        raise ValueError("Actor cameras differ from the declared profile")
    validate_vector(observation.state, profile.observation_fields, "observation")
    if set(observation.history) != set(profile.history):
        raise ValueError("Observation history differs from the declared streams")
    for camera, requirement in profile.history.items():
        frames = observation.history[camera]
        if not requirement.min_frames <= len(frames) <= requirement.max_frames:
            raise ValueError("Observation history length is outside its declared bounds")
        if len({frame.frame_id for frame in frames}) != len(frames):
            raise ValueError("History frame identities must be unique")
        for before, after in zip(frames, frames[1:]):
            if after.simulation_time_s <= before.simulation_time_s or after.state_version < before.state_version:
                raise ValueError("History must follow the source simulation clock and state order")


class ActorInfo(Contract):
    protocol: Literal["uni_robot_agent.actor.v3"] = PROTOCOL
    model: str
    runtime: str
    model_id: str
    checkpoint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_config_name: str
    profile: ActorProfile | SymbolicActorProfile
    policy_rng_seed: int | None = None


class ActorObservation(Contract):
    instruction: str
    state: list[FiniteFloat] = Field(max_length=1024)
    images: dict[str, Image]
    history: dict[str, list[HistoryFrame]] = Field(default_factory=dict)


class CreateSession(Contract):
    protocol: Literal["uni_robot_agent.actor.v3"] = PROTOCOL
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
    prediction: Prediction

    @property
    def actions(self) -> list[list[float]]:
        """Retain the numeric client view without interpreting symbolic predictions."""
        if not isinstance(self.prediction, NumericChunk):
            raise TypeError("This actor returned a symbolic prediction")
        return self.prediction.actions


class Receipt(Contract):
    session_id: str
    request_id: str
    sequence: int = Field(ge=0)
    status: Literal["running", "succeeded", "failed"]
    reply: PredictReply | None = None
    error: str | None = None
