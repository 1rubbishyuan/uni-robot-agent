"""Frozen sensor frames shared by capture, action records, and observers."""

from __future__ import annotations

import copy
import io
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from types import MappingProxyType

import numpy as np
from PIL import Image

from uni_robot_agent.artifacts.store import ArtifactStore


@dataclass(frozen=True)
class CameraFrame:
    rgb: np.ndarray
    depth_m: np.ndarray | None = None
    intrinsic_K: np.ndarray | None = None
    T_world_from_optical: np.ndarray | None = None
    calibration: Mapping[str, Any] | None = None

    def freeze(self) -> CameraFrame:
        rgb = np.array(self.rgb, dtype=np.uint8, copy=True)
        if rgb.ndim != 3 or rgb.shape[2] != 3:
            raise ValueError("RGB must have shape [height, width, 3]")
        depth = _array(self.depth_m, np.float32)
        intrinsic = _array(self.intrinsic_K, np.float64)
        extrinsic = _array(self.T_world_from_optical, np.float64)
        if depth is not None and depth.shape != rgb.shape[:2]:
            raise ValueError("Depth and RGB must have the same dimensions")
        if (intrinsic is None) != (extrinsic is None):
            raise ValueError("Camera calibration requires both K and optical-to-world")
        if intrinsic is not None:
            if intrinsic.shape != (3, 3) or extrinsic.shape != (4, 4):
                raise ValueError("Camera calibration must have shapes [3,3] and [4,4]")
            if not np.isfinite(intrinsic).all() or not np.isfinite(extrinsic).all():
                raise ValueError("Camera calibration must be finite")
        for value in (rgb, depth, intrinsic, extrinsic):
            if value is not None:
                value.setflags(write=False)
        return CameraFrame(rgb, depth, intrinsic, extrinsic, _metadata(self.calibration, freeze=True))


@dataclass(frozen=True)
class CapturedObservation:
    episode_id: str
    state_version: int
    captured_at: float
    state: Mapping[str, Any]
    cameras: Mapping[str, CameraFrame]
    terminated: bool = False
    truncated: bool = False

    def freeze(self) -> CapturedObservation:
        return CapturedObservation(
            self.episode_id, self.state_version, self.captured_at,
            copy.deepcopy(dict(self.state)),
            {name: frame.freeze() for name, frame in self.cameras.items()},
            self.terminated, self.truncated,
        )


class SnapshotStore:
    """Publish immutable snapshots without holding an environment execution lock."""

    def __init__(self, artifacts: ArtifactStore):
        self.artifacts = artifacts
        self._lock = threading.RLock()
        self._latest: str | None = None
        self._records: list[str] = []

    def publish(self, observation: CapturedObservation, *, record: bool = False) -> dict:
        observation = observation.freeze()
        cameras = {}
        # Encoding and persistence run after copying; native buffers are no longer accessed.
        for name, frame in observation.cameras.items():
            stream = io.BytesIO()
            Image.fromarray(frame.rgb).save(stream, format="PNG")
            cameras[name] = {
                "rgb": self.artifacts.put_bytes(stream.getvalue(), "image/png"),
                "image_size": list(frame.rgb.shape[:2]),
                "depth_m": None if frame.depth_m is None else self.artifacts.put_array(frame.depth_m),
                "intrinsic_K": None if frame.intrinsic_K is None else frame.intrinsic_K.tolist(),
                "T_world_from_optical": (
                    None if frame.T_world_from_optical is None else frame.T_world_from_optical.tolist()
                ),
            }
            if frame.calibration is not None:
                cameras[name]["calibration"] = _metadata(frame.calibration)
        manifest = {
            "artifact_type": "environment_snapshot", "schema_version": 2,
            "episode_id": observation.episode_id,
            "state_version": observation.state_version,
            "captured_at": observation.captured_at,
            "state": dict(observation.state), "cameras": cameras,
            "terminated": observation.terminated, "truncated": observation.truncated,
        }
        ref = self.artifacts.put_json(manifest)
        snapshot_id = ref["artifact_id"]
        with self._lock:
            self._latest = snapshot_id
            if record:
                self._records.append(snapshot_id)
        return dict(manifest, snapshot_id=snapshot_id, manifest=ref)

    def get(self, snapshot_id: str) -> dict:
        try:
            manifest = self.artifacts.read_json(snapshot_id)
        except (ValueError, UnicodeError) as error:
            raise LookupError(f"Artifact is not a snapshot: {snapshot_id}") from error
        if not isinstance(manifest, dict) or manifest.get("artifact_type") != "environment_snapshot":
            raise LookupError(f"Artifact is not a snapshot: {snapshot_id}")
        if manifest.get("schema_version") != 2:
            raise LookupError(f"Unsupported snapshot schema: {snapshot_id}")
        return dict(manifest, snapshot_id=snapshot_id, manifest=self.artifacts.describe(snapshot_id))

    def latest(self) -> dict:
        with self._lock:
            snapshot_id = self._latest
        if snapshot_id is None:
            raise LookupError("No observation has been published")
        return self.get(snapshot_id)

    def record(self, index: int = -1) -> dict:
        with self._lock:
            if index < -1 or not self._records or index >= len(self._records):
                raise LookupError(f"Unknown completed record: {index}")
            actual_index = len(self._records) - 1 if index == -1 else index
            snapshot_id = self._records[actual_index]
        return dict(self.get(snapshot_id), record_index=actual_index)

    def frame(self, snapshot_id: str, camera: str) -> CameraFrame:
        payload = self.get(snapshot_id)["cameras"][camera]
        rgb = np.asarray(Image.open(io.BytesIO(self.artifacts.read_bytes(payload["rgb"]["artifact_id"]))))
        depth = payload["depth_m"]
        return CameraFrame(
            rgb, None if depth is None else self.artifacts.read_array(depth["artifact_id"]),
            payload["intrinsic_K"], payload["T_world_from_optical"], payload.get("calibration"),
        ).freeze()


def _array(value, dtype):
    return None if value is None else np.array(value, dtype=dtype, copy=True)


def _metadata(value, *, freeze=False):
    if isinstance(value, Mapping):
        result = {key: _metadata(item, freeze=freeze) for key, item in value.items()}
        return MappingProxyType(result) if freeze else result
    if isinstance(value, (list, tuple)):
        result = [_metadata(item, freeze=freeze) for item in value]
        return tuple(result) if freeze else result
    return copy.deepcopy(value)
