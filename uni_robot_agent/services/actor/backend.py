"""Local model boundary; one backend instance belongs to one actor service."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from .contracts import ActorInfo, ActorObservation


@dataclass(frozen=True)
class ActorWork:
    state: Any
    observation: ActorObservation


class ActorBackend(Protocol):
    @property
    def info(self) -> ActorInfo: ...
    def create_session(self) -> Any: ...
    def close_session(self, state: Any) -> None: ...
    def predict_batch(
        self, requests: Sequence[ActorWork]
    ) -> list[list[list[float]] | Exception]: ...
    def close(self) -> None: ...
