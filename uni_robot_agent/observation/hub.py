"""Environment snapshot protocols, file storage, and coordination.

ObservationProvider owns sampling; the hub owns neither the environment
nor sampling frequency, only versioning, concurrency, and persistence.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol

from uni_robot_agent.execution import ToolLifecycleEvent


@dataclass(frozen=True)
class ObservationData:
    """Environment data captured by one provider sample.

    Image paths refer to immutable files in the session artifact store. Source
    versions describe physical state; they are distinct from tool mutation epochs.
    """

    source: str
    state: Mapping[str, Any]
    image_paths: Mapping[str, str] = field(default_factory=dict)
    terminated: bool = False
    truncated: bool = False
    source_episode_id: str | None = None
    source_snapshot_id: str | None = None
    source_state_version: int | None = None
    source_captured_at: float | None = None
    source_current_state_version: int | None = None


class ObservationProvider(Protocol):
    """Sample an environment without managing versions or file indexes."""

    def capture(self) -> ObservationData:
        ...


@dataclass(frozen=True)
class ObservationSnapshot:
    """Immutable environment snapshot persisted to storage."""

    observation_id: str
    observation_seq: int
    mutation_epoch: int
    source: str
    timestamp_unix_s: float
    state_path: str
    image_paths: Mapping[str, str]
    terminated: bool
    truncated: bool
    source_episode_id: str | None = None
    source_snapshot_id: str | None = None
    source_state_version: int | None = None
    source_captured_at: float | None = None
    source_current_state_version: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "observation_seq": self.observation_seq,
            "world_version": self.mutation_epoch,
            "source": self.source,
            "timestamp": self.timestamp_unix_s,
            "state_path": self.state_path,
            "image_paths": dict(self.image_paths),
            "terminated": self.terminated,
            "truncated": self.truncated,
            "source_episode_id": self.source_episode_id,
            "source_snapshot_id": self.source_snapshot_id,
            "source_state_version": self.source_state_version,
            "source_captured_at": self.source_captured_at,
            "source_current_state_version": self.source_current_state_version,
        }


@dataclass(frozen=True)
class InsightRecord:
    """Structured observer insight referencing snapshots rather than embedding images."""

    insight_id: str
    insight_seq: int
    observer_id: str
    observation_id: str
    mutation_epoch: int
    timestamp_unix_s: float
    facts: tuple[str, ...] = ()
    changes: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    confidence: float | None = None
    summary: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "insight_id": self.insight_id,
            "insight_seq": self.insight_seq,
            "observer_id": self.observer_id,
            "observation_id": self.observation_id,
            "world_version": self.mutation_epoch,
            "timestamp": self.timestamp_unix_s,
            "facts": list(self.facts),
            "changes": list(self.changes),
            "risks": list(self.risks),
            "confidence": self.confidence,
            "summary": self.summary,
        }


class InsightStore:
    """Append insights per observer and support incremental reads by cursor."""

    _ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")

    def __init__(self, root: str | Path) -> None:
        self._directory = Path(root) / "observers"
        self._directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._records: dict[str, list[InsightRecord]] = {}

    def publish(
        self,
        *,
        observer_id: str,
        observation_id: str,
        mutation_epoch: int,
        facts: list[str] | tuple[str, ...],
        changes: list[str] | tuple[str, ...] = (),
        risks: list[str] | tuple[str, ...] = (),
        confidence: float | None = None,
        summary: str | None = None,
    ) -> InsightRecord:
        with self._lock:
            records = self._records_for(observer_id)
            sequence = len(records) + 1
            record = InsightRecord(
                insight_id=f"{observer_id}_{sequence:06d}",
                insight_seq=sequence,
                observer_id=observer_id,
                observation_id=observation_id,
                mutation_epoch=int(mutation_epoch),
                timestamp_unix_s=time.time(),
                facts=tuple(facts),
                changes=tuple(changes),
                risks=tuple(risks),
                confidence=confidence,
                summary=summary,
            )
            path = self._path_for(observer_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
            records.append(record)
            return record

    def latest(self, observer_id: str) -> InsightRecord | None:
        with self._lock:
            records = self._records_for(observer_id)
            return records[-1] if records else None

    def read(
        self, observer_id: str, *, after_seq: int = 0, limit: int = 20,
    ) -> list[InsightRecord]:
        with self._lock:
            return [
                item for item in self._records_for(observer_id)
                if item.insight_seq > after_seq
            ][:limit]

    def _records_for(self, observer_id: str) -> list[InsightRecord]:
        self._validate_id(observer_id)
        records = self._records.get(observer_id)
        if records is not None:
            return records
        records = []
        path = self._path_for(observer_id)
        if path.exists():
            with path.open(encoding="utf-8") as stream:
                for line in stream:
                    if line.strip():
                        payload = json.loads(line)
                        payload["mutation_epoch"] = payload.pop("world_version")
                        payload["timestamp_unix_s"] = payload.pop("timestamp")
                        records.append(InsightRecord(
                            **{
                                **payload,
                                "facts": tuple(payload.get("facts", ())),
                                "changes": tuple(payload.get("changes", ())),
                                "risks": tuple(payload.get("risks", ())),
                            }
                        ))
        self._records[observer_id] = records
        return records

    def _path_for(self, observer_id: str) -> Path:
        self._validate_id(observer_id)
        return self._directory / observer_id / "updates.jsonl"

    @classmethod
    def _validate_id(cls, value: str) -> None:
        if not value or not cls._ID_PATTERN.fullmatch(value):
            raise ValueError(f"invalid observer_id: {value!r}")


class ObservationStore:
    """Persist immutable observations as JSON with image-path references."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()
        self._directory = self._root / "observations"
        self._directory.mkdir(parents=True, exist_ok=True)
        self._index_path = self._directory / "index.jsonl"
        self._lock = threading.RLock()
        self._snapshots: list[ObservationSnapshot] = []
        self._load_index()

    @property
    def root(self) -> Path:
        return self._root

    def save(self, data: ObservationData, mutation_epoch: int) -> ObservationSnapshot:
        """Persist a sample and return its stable snapshot reference."""
        with self._lock:
            sequence = len(self._snapshots) + 1
            observation_id = f"obs_{sequence:06d}"
            directory = self._directory / observation_id
            directory.mkdir(parents=True, exist_ok=False)

            state_path = directory / "state.json"
            _atomic_write_json(state_path, dict(data.state))
            image_paths = {name: str((self._root / path).resolve())
                           for name, path in data.image_paths.items()}

            snapshot = ObservationSnapshot(
                observation_id=observation_id,
                observation_seq=sequence,
                mutation_epoch=mutation_epoch,
                source=data.source,
                timestamp_unix_s=time.time(),
                state_path=str(state_path.resolve()),
                image_paths=image_paths,
                terminated=data.terminated,
                truncated=data.truncated,
                source_episode_id=data.source_episode_id,
                source_snapshot_id=data.source_snapshot_id,
                source_state_version=data.source_state_version,
                source_captured_at=data.source_captured_at,
                source_current_state_version=data.source_current_state_version,
            )
            record = snapshot.to_dict()
            record["state_path"] = str(state_path.relative_to(self._root))
            record["image_paths"] = {name: str(Path(path).relative_to(self._root))
                                     for name, path in image_paths.items()}
            _atomic_append_json(self._index_path, record)
            self._snapshots.append(snapshot)
            return snapshot

    def latest(self) -> ObservationSnapshot | None:
        with self._lock:
            return self._snapshots[-1] if self._snapshots else None

    def get(self, observation_id: str) -> ObservationSnapshot | None:
        with self._lock:
            return next(
                (item for item in self._snapshots if item.observation_id == observation_id),
                None,
            )

    def list(self, *, after_seq: int = 0, limit: int = 20) -> list[ObservationSnapshot]:
        with self._lock:
            items = [item for item in self._snapshots if item.observation_seq > after_seq]
            return items[:limit]

    def _load_index(self) -> None:
        if not self._index_path.exists():
            return
        with self._index_path.open(encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    fields = _snapshot_fields(json.loads(line))
                    fields["state_path"] = str(self._root / fields["state_path"])
                    fields["image_paths"] = {name: str(self._root / path)
                                              for name, path in fields["image_paths"].items()}
                    self._snapshots.append(ObservationSnapshot(**fields))


class ObservationHub:
    """Connect provider and store while tracking world versions and freshness."""

    def __init__(self, provider: ObservationProvider, root: str | Path) -> None:
        self._provider = provider
        self._store = ObservationStore(root)
        self._insights = InsightStore(root)
        self._lock = threading.RLock()
        self._capture_lock = threading.Lock()
        latest = self._store.latest()
        self._mutation_epoch = latest.mutation_epoch if latest else 0
        self._active_executions: dict[str, str] = {}

    def on_tool_event(self, event: ToolLifecycleEvent) -> None:
        """Handle execution lifecycle events without propagating observation errors into execution."""
        if not event.mutates_world:
            return
        with self._lock:
            if event.phase == "started":
                self._active_executions[event.execution_id] = event.tool_name
            else:
                self._active_executions.pop(event.execution_id, None)
                # A failed action may still have moved the arm or changed the gripper.
                self._mutation_epoch += 1

    def capture_observation(self) -> dict[str, Any]:
        """Sample actively, including while physical actions are running.

        The provider guarantees internal consistency, such as an environment's atomic
        server snapshot. The hub labels freshness relative to world versions;
        rejecting samples during motion would prevent concurrent observation.
        """
        # Providers may read mutable environments; serialize sampling within each hub.
        with self._capture_lock:
            with self._lock:
                mutation_epoch = self._mutation_epoch

            data = self._provider.capture()

            with self._lock:
                # The world may advance during sampling. Label the resulting older version
                # stale rather than claiming it is fresh.
                crossed = self._mutation_epoch != mutation_epoch
                snapshot = self._store.save(data, mutation_epoch)
                payload = self._snapshot_payload_locked(snapshot)
                if crossed and payload["freshness"] == "fresh":
                    payload["freshness"] = "stale"
                return {"captured": True, **payload}

    def latest_observation(self) -> dict[str, Any]:
        with self._lock:
            return self._latest_payload_locked()

    def get_observation(self, observation_id: str) -> dict[str, Any]:
        snapshot = self._store.get(observation_id)
        if snapshot is None:
            raise KeyError(f"Unknown observation: {observation_id}")
        with self._lock:
            return self._snapshot_payload_locked(snapshot)

    def list_observations(self, *, after_seq: int = 0, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            return [
                self._snapshot_payload_locked(item)
                for item in self._store.list(after_seq=after_seq, limit=limit)
            ]

    def publish_insight(self, **kwargs: Any) -> dict[str, Any]:
        """Save observer insights; let the tool report persistence failures to its caller."""
        kwargs["mutation_epoch"] = kwargs.pop("world_version")
        return self._insights.publish(**kwargs).to_dict()

    def latest_insight(self, observer_id: str) -> dict[str, Any] | None:
        record = self._insights.latest(observer_id)
        return record.to_dict() if record else None

    def read_insight_updates(
        self, observer_id: str, *, after_seq: int = 0, limit: int = 20,
    ) -> list[dict[str, Any]]:
        return [
            item.to_dict()
            for item in self._insights.read(
                observer_id, after_seq=after_seq, limit=limit,
            )
        ]

    def close(self) -> None:
        """Keep the lifecycle interface; this file store owns no background resources."""

    def _latest_payload_locked(self) -> dict[str, Any]:
        snapshot = self._store.latest()
        if snapshot is None:
            return {
                "observation": None,
                "freshness": "unknown",
                "current_world_version": self._mutation_epoch,
                "in_flight_jobs": sorted(self._active_executions),
            }
        return self._snapshot_payload_locked(snapshot)

    def _snapshot_payload_locked(self, snapshot: ObservationSnapshot) -> dict[str, Any]:
        if self._active_executions:
            freshness = "in_flight"
        elif snapshot.mutation_epoch == self._mutation_epoch:
            freshness = "fresh"
        else:
            freshness = "stale"
        if (freshness == "fresh" and snapshot.source_state_version is not None
                and snapshot.source_current_state_version is not None
                and snapshot.source_state_version < snapshot.source_current_state_version):
            freshness = "stale"
        captured_at = snapshot.source_captured_at or snapshot.timestamp_unix_s
        return {
            "observation": snapshot.to_dict(),
            "freshness": freshness,
            "current_world_version": self._mutation_epoch,
            "age_ms": round(max(0.0, time.time() - captured_at) * 1000, 1),
            "in_flight_jobs": sorted(self._active_executions),
        }


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _atomic_append_json(path: Path, value: Mapping[str, Any]) -> None:
    # This process serializes index writes; line records support inspection and incremental reads.
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + "\n")


def _snapshot_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """Read the stable observation schema into its internal Python names."""
    fields = dict(payload)
    fields["mutation_epoch"] = fields.pop("world_version")
    fields["timestamp_unix_s"] = fields.pop("timestamp")
    return fields
