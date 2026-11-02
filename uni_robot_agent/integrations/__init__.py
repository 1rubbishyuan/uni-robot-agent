"""Environment integration registration and session contracts."""

from __future__ import annotations

import importlib
from typing import Any

from uni_robot_agent.integrations.base import (
    EpisodeOutcome,
    IntegrationContext,
    IntegrationSession,
)

_INTEGRATION_REGISTRY: dict[str, Any] = {}


def load_integration(name: str) -> Any:
    """Load an integration lazily and validate its session entry point."""
    if name not in _INTEGRATION_REGISTRY:
        # Integrations expose their session factories through one namespace.
        module_name = f"uni_robot_agent.integrations.{name}"
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as error:
            if error.name != module_name:
                raise
            raise ValueError(f"Integration {name!r} is not available in this installation") from error
        if not callable(getattr(module, "get_integration_spec", None)):
            raise ValueError(f"Integration {name!r} is not available in this installation")
        spec = module.get_integration_spec()
        if not callable(getattr(spec, "create_session", None)):
            raise AttributeError(f"uni_robot_agent.integrations.{name} does not provide create_session")
        _INTEGRATION_REGISTRY[name] = module
    return _INTEGRATION_REGISTRY[name]


__all__ = [
    "EpisodeOutcome", "IntegrationContext", "IntegrationSession",
    "load_integration",
]
