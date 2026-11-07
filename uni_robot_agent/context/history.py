"""Model context contracts, independent of trimming algorithms."""

from __future__ import annotations

import copy
import importlib
import json
import threading
from dataclasses import dataclass, field, replace
from typing import Any, Protocol


@dataclass(frozen=True)
class ContextRequest:
    """Native wire request snapshot consumed by a context policy."""

    body: dict[str, Any]
    preserve_history: bool = False
    wire_format: str = "responses"

    @classmethod
    def from_dict(cls, body: dict[str, Any]) -> "ContextRequest":
        return cls(copy.deepcopy(body))

    @property
    def items_key(self) -> str:
        return {"gemini": "contents", "messages": "messages"}.get(self.wire_format, "input")

    @property
    def items(self) -> tuple[Any, ...]:
        value = self.body.get(self.items_key, [])
        return tuple(value) if isinstance(value, list) else ()

    def with_items(self, items: list[Any]) -> "ContextRequest":
        body = copy.deepcopy(self.body)
        body[self.items_key] = items
        return replace(self, body=body)


@dataclass(frozen=True)
class ContextBlock:
    """Smallest trim unit; tool calls and their results stay in the same block."""

    item_ids: tuple[str, ...]
    items: tuple[Any, ...]
    kind: str
    pinned: bool = False
    observation: bool = False


@dataclass(frozen=True)
class ContextBudget:
    max_estimated_input_tokens: int
    target_estimated_input_tokens: int
    reserve_output_tokens: int = 0
    keep_recent_blocks: int = 4
    keep_observation_blocks: int = 1

    def __post_init__(self) -> None:
        if self.max_estimated_input_tokens < 1:
            raise ValueError("max_estimated_input_tokens must be positive")
        if not 0 < self.target_estimated_input_tokens <= self.max_estimated_input_tokens:
            raise ValueError("target_estimated_input_tokens must be in (0, max_estimated_input_tokens]")
        if self.reserve_output_tokens < 0:
            raise ValueError("reserve_output_tokens cannot be negative")
        if self.reserve_output_tokens >= self.max_estimated_input_tokens:
            raise ValueError(
                "reserve_output_tokens must be smaller than max_estimated_input_tokens"
            )
        if self.keep_recent_blocks < 0 or self.keep_observation_blocks < 0:
            raise ValueError("retention counts cannot be negative")

    @property
    def trigger_tokens(self) -> int:
        return self.max_estimated_input_tokens - self.reserve_output_tokens

    @property
    def target_tokens(self) -> int:
        return min(self.target_estimated_input_tokens, self.trigger_tokens)


@dataclass(frozen=True)
class ContextDecision:
    request: ContextRequest
    policy: str
    version: str
    truncated: bool
    estimated_tokens_before: int
    estimated_tokens_after: int
    kept_item_ids: tuple[str, ...]
    dropped_item_ids: tuple[str, ...] = ()
    reason: str = ""
    decision_id: str = ""
    options: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.policy,
            "wire_format": self.request.wire_format,
            "version": self.version,
            "decision_id": self.decision_id,
            "truncated": self.truncated,
            "estimated_tokens_before": self.estimated_tokens_before,
            "estimated_tokens_after": self.estimated_tokens_after,
            "kept_item_ids": list(self.kept_item_ids),
            "dropped_item_ids": list(self.dropped_item_ids),
            "reason": self.reason,
            "options": dict(self.options),
        }


@dataclass(frozen=True)
class PreparedContext:
    original: dict[str, Any]
    effective: dict[str, Any]
    decision: ContextDecision


class ContextPolicy(Protocol):
    name: str
    version: str

    @property
    def options(self) -> dict[str, Any]: ...

    def select(self, request: ContextRequest) -> ContextDecision: ...


def estimate_tokens(value: object) -> int:
    """Return a stable conservative estimate; policies may supply an exact tokenizer."""
    encoded = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), default=str
    ).encode()
    return max(1, (len(encoded) + 3) // 4)


_CONTEXT_POLICY_REGISTRY: dict[str, Any] = {}


def register_context_policy(name: str):
    """Register a policy factory that creates a fresh instance per session."""
    if not name or not name.isidentifier():
        raise ValueError(f"invalid context policy name: {name!r}")

    def wrapper(factory):
        existing = _CONTEXT_POLICY_REGISTRY.get(name)
        if existing is not None and existing is not factory:
            raise ValueError(f"context policy already registered: {name}")
        _CONTEXT_POLICY_REGISTRY[name] = factory
        return factory

    return wrapper


def create_context_policy(name: str, **options):
    """Create a fresh context policy by name."""
    if name not in _CONTEXT_POLICY_REGISTRY:
        try:
            importlib.import_module(f"uni_robot_agent.context.policies.{name}")
        except ModuleNotFoundError as exc:
            module_name = f"uni_robot_agent.context.policies.{name}"
            if exc.name != module_name:
                raise
            available = ", ".join(list_context_policies())
            raise ValueError(
                f"Unknown context policy: {name!r}. Available: {available}"
            ) from exc
    return _CONTEXT_POLICY_REGISTRY[name](**options)


def list_context_policies() -> list[str]:
    """List registered context policies."""
    importlib.import_module("uni_robot_agent.context.policies")
    return sorted(_CONTEXT_POLICY_REGISTRY)


class ContextManager:
    """Use one instance per provider session to isolate mutable state."""

    def __init__(self, policy: ContextPolicy) -> None:
        self._policy = policy
        self._lock = threading.Lock()
        self._next_id = 0

    @property
    def policy_metadata(self) -> dict[str, Any]:
        """Expose the selected policy in traces even when prepare fails."""
        return {
            "name": self._policy.name,
            "version": self._policy.version,
            "options": dict(self._policy.options),
        }

    def prepare(
        self, body: dict[str, Any], *, preserve_history: bool = False,
        wire_format: str = "responses",
    ) -> PreparedContext:
        original = copy.deepcopy(body)
        request = replace(
            ContextRequest.from_dict(body),
            preserve_history=preserve_history,
            wire_format=wire_format,
        )
        decision = self._policy.select(request)

        with self._lock:
            decision_id = f"ctx_{self._next_id:04d}"
            self._next_id += 1
        decision = replace(decision, decision_id=decision_id)
        return PreparedContext(
            original=original,
            effective=copy.deepcopy(decision.request.body),
            decision=decision,
        )
