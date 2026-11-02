"""Optional observation services, created only when a profile is selected."""

from __future__ import annotations

import importlib
from typing import Any

from uni_robot_agent.observation.hub import (
    InsightRecord,
    InsightStore,
    ObservationData,
    ObservationHub,
    ObservationProvider,
    ObservationSnapshot,
    ObservationStore,
)


def load_observation_profile(name: str) -> Any:
    """Load an optional observation profile independently of integration registration."""
    module = importlib.import_module(f"uni_robot_agent.integrations.{name}")
    if not callable(getattr(module, "create_observation_profile", None)):
        raise ValueError(
            f"observation profile uni_robot_agent.integrations.{name} is missing create_observation_profile()"
        )
    return module

__all__ = [
    "InsightRecord",
    "InsightStore",
    "ObservationData",
    "ObservationHub",
    "ObservationProvider",
    "ObservationSnapshot",
    "ObservationStore",
]
