"""Materialized observations for integration-owned actor bindings."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class HistoryImage:
    frame_id: str
    captured_at: float
    simulation_time_s: float
    state_version: int
    path: str


@dataclass(frozen=True)
class ObservationInputs:
    manifest: dict
    images: dict[str, str]
    history: dict[str, list[HistoryImage]] = field(default_factory=dict)

    @classmethod
    def capture(cls, episode, manifest):
        history = {
            name: [HistoryImage(frame_id=sample["frame_id"], captured_at=sample["captured_at"],
                                simulation_time_s=sample["simulation_time_s"], state_version=sample["state_version"],
                                path=str(episode.artifact_mirror.local_path(sample["rgb"])))
                   for sample in samples]
            for name, samples in manifest.get("history", {}).items()
        }
        return cls(manifest=manifest, images=episode.image_paths(manifest), history=history)
