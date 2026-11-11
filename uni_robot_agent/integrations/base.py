"""Minimal lifecycle contracts for one integration run."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from uni_robot_agent.artifacts.store import ArtifactStore
from uni_robot_agent.integrations.composition import ToolAssembly, ToolModule
from uni_robot_agent.tools.toolkit import ToolProvider


def _add_no_cli_args(_parser: argparse.ArgumentParser) -> None:
    """Default implementation for integrations without environment-specific arguments."""


def _validate_no_cli_args(_args: Any) -> None:
    """Default integrations impose no additional argument-combination constraints."""


class IntegrationOptions(BaseModel):
    """Typed options for Python callers; integrations extend this schema locally."""

    model_config = ConfigDict(extra="forbid", frozen=True)


@dataclass
class IntegrationSpec:
    """Static integration configuration and its single session entry point."""

    name: str
    create_session: Callable[[Any], Any]
    add_cli_args: Callable[[argparse.ArgumentParser], None] = _add_no_cli_args
    tool_profiles: Mapping[str, tuple[ToolModule, ...]] = field(default_factory=dict)
    default_tool_profile: str = "direct"
    debug_modules: tuple[ToolModule, ...] = ()
    validate_cli: Callable[[Any], None] = _validate_no_cli_args
    allow_automatic_retry: bool = True
    # Execution mode used when the run does not choose one (see execution.EXECUTION_MODES).
    execution_mode: str = "parallel"
    requires_env_url: bool = True
    deployment_module: str | None = None
    config_model: type[IntegrationOptions] = IntegrationOptions


@dataclass(frozen=True)
class IntegrationContext:
    """Runtime information supplied by the harness to an integration."""

    args: Any
    session_dir: Path
    artifacts: ArtifactStore
    tracer: Any
    tool_modules: tuple[ToolModule, ...]


@dataclass(frozen=True)
class EpisodeOutcome:
    """Official episode outcome after finalization."""

    success: bool | None
    accepted: bool = True
    retryable: bool = False
    failure_category: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.retryable and self.accepted:
            raise ValueError("retryable outcome must not enter the evaluation denominator")


class IntegrationSession(Protocol):
    """Own environment and model connections, toolkits, and the episode lifecycle."""

    @property
    def tool_assembly(self) -> ToolAssembly: ...

    @property
    def tool_providers(self) -> tuple[ToolProvider, ...]: ...

    @property
    def episode(self) -> Any: ...

    def task_succeeded(self) -> bool | None: ...

    @property
    def action_model_name(self) -> str | None: ...

    def start(self) -> None: ...

    def finalize(self, *, agent_result: Any, error: str | None) -> EpisodeOutcome: ...

    def close(self) -> None: ...
