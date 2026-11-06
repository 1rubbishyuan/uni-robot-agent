"""Device-independent contracts for bounded actor collaboration."""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Protocol

from uni_robot_agent.services.actor.contracts import ActorObservation


@dataclass(frozen=True)
class HybridCapabilities:
    groups: tuple[str, ...]
    constraint_schema: dict
    corrections: dict[str, dict] = field(default_factory=dict)
    default_actions: int = 25
    max_actions: int = 40
    default_max_chunks: int = 6
    max_correction_actions: int = 5

    def __post_init__(self):
        if not self.groups or len(set(self.groups)) != len(self.groups):
            raise ValueError("Hybrid groups must be nonempty and distinct")
        if not 1 <= self.default_actions <= self.max_actions or self.default_max_chunks < 1:
            raise ValueError("Invalid hybrid execution budgets")


@dataclass
class Proposal:
    """Decoded proposal and evidence, without actor or execution ownership."""
    rows: list[Any]
    state_version: int
    current_state: dict
    images: dict[str, str]
    raw_actions: Any
    scoped_actions: Any
    metadata: dict = field(default_factory=dict)
    preview: dict = field(default_factory=dict)


@dataclass(frozen=True)
class StepOutcome:
    """What one acknowledged primitive establishes about one proposal sample."""
    status: str
    completed_samples: int = 0
    execution_started: bool = False
    duration_s: float | None = None
    stop_reason: str | None = None
    feedback: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.status not in {"completed", "partial", "rejected", "unknown"}:
            raise ValueError("Invalid hybrid primitive outcome")
        if type(self.completed_samples) is not int or self.completed_samples not in (0, 1):
            raise ValueError("One primitive may acknowledge at most one proposal sample")
        if self.status != "completed" and self.completed_samples:
            raise ValueError("Only a completed primitive establishes a completed sample")
        if self.duration_s is not None and (not math.isfinite(self.duration_s) or self.duration_s < 0):
            raise ValueError("An acknowledged duration must be finite and nonnegative")


@dataclass
class ExecutionResult:
    requested_samples: int
    completed_samples: int = 0
    acknowledged_commands: int = 0
    execution_started: bool = False
    duration_s: float = 0.
    duration_known: bool = True
    status: str = "completed"
    stop_reason: str = "requested_sample_budget"
    error: str | None = None
    feedback: dict = field(default_factory=dict)
    last_reply: Any = None

    def public(self):
        return {**self.feedback, "requested_actions": self.requested_samples, "executed_actions": self.completed_samples,
                "acknowledged_commands": self.acknowledged_commands,
                "execution_started": self.execution_started,
                "duration_s": self.duration_s if self.duration_known else None,
                "execution_status": self.status, "stop_reason": self.stop_reason,
                "no_execution": not self.execution_started and self.status != "unknown",
                "execution_uncertain": self.status == "unknown", "error": self.error,
                "discarded_remaining_proposal": True}


class HybridBinding(Protocol):
    """Semantic adaptation; model calls and prefix loops belong to the tools."""
    name: str
    capabilities: HybridCapabilities
    action_horizon: int
    reviewer_prompt: Any
    reviewer_supported: bool

    def validate_scope(self, active_groups: list[str], constraints: dict) -> dict: ...
    def encode_observation(self, manifest: dict, instruction: str, images: dict[str, str]) -> ActorObservation: ...
    def decode_prediction(self, actions, manifest: dict, images: dict[str, str], scope: dict) -> Proposal: ...
    def preview(self, proposal: Proposal, environment) -> dict: ...
    def review_context(self, proposal: Proposal, scope: dict) -> dict: ...
    def validate_decision(self, decision: dict, context: dict, actions_per_chunk: int) -> dict: ...
    def plan(self, proposal: Proposal, decision: dict, scope: dict) -> list[Any]: ...
    def compile_row(self, row: Any, state: Any) -> Any: ...
    def dispatch(self, environment, command: Any, expected_state_version: int) -> Any: ...
    def outcome(self, reply: Any, command: Any) -> StepOutcome: ...
