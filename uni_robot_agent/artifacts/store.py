"""Content-addressed artifacts with readable references and explicit persistence state."""

from __future__ import annotations

import hashlib
import io
import json
import os
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np

from uni_robot_agent.artifacts.writer import AsyncPersistence


class ArtifactStore:
    """Keep published bytes readable while their optional durable copy is written."""

    def __init__(self, root: str | Path | None = None, *, persistence=None,
                 max_memory_bytes: int = 64 * 1024 * 1024):
        self.root = Path(root).resolve() if root is not None else None
        if self.root is not None:
            (self.root / "artifacts").mkdir(parents=True, exist_ok=True)
            (self.root / "images").mkdir(exist_ok=True)
        self._writer = persistence
        self._owns_writer = self.root is not None and persistence is None
        if self._owns_writer:
            self._writer = AsyncPersistence(name="episode-artifacts")
        self._lock = threading.RLock()
        self._ready = threading.Condition(self._lock)
        self._max_memory_bytes = max(0, int(max_memory_bytes))
        self._bytes: OrderedDict[str, bytes] = OrderedDict()
        self._memory_bytes = 0
        self._metadata: dict[str, dict[str, Any]] = {}

    def put_bytes(self, data: bytes, media_type: str = "application/octet-stream") -> dict:
        data = bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        artifact_id = hashlib.sha256(media_type.encode() + b"\0" + data).hexdigest()
        with self._lock:
            if artifact_id in self._metadata:
                return dict(self._metadata[artifact_id])
            ref = {
                "artifact_id": artifact_id, "media_type": media_type,
                "size_bytes": len(data), "sha256": digest,
                "persistence": "pending" if self.root is not None else "memory",
            }
            self._bytes[artifact_id] = data
            self._memory_bytes += len(data)
            self._metadata[artifact_id] = ref
        if self.root is not None:
            try:
                self._writer.submit(
                    artifact_id, lambda: self._persist(artifact_id, data),
                    estimated_bytes=len(data),
                )
            except Exception as error:
                self._failed(artifact_id, error)
        return self.describe(artifact_id)

    def put_json(self, value: Any) -> dict:
        data = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True).encode()
        return self.put_bytes(data, "application/json")

    def put_array(self, value: np.ndarray) -> dict:
        array = np.asarray(value)
        if array.dtype.hasobject:
            raise ValueError("artifact arrays cannot contain Python objects")
        stream = io.BytesIO()
        np.save(stream, array, allow_pickle=False)
        return self.put_bytes(stream.getvalue(), "application/x-npy")

    def read_bytes(self, artifact_id: str) -> bytes:
        self._validate_id(artifact_id)
        with self._lock:
            data = self._bytes.get(artifact_id)
            if data is not None:
                self._bytes.move_to_end(artifact_id)
        if data is not None:
            return data
        if self.root is None:
            raise KeyError(f"Unknown artifact: {artifact_id}")
        try:
            return self.local_path(artifact_id).read_bytes()
        except FileNotFoundError as error:
            raise KeyError(f"Unknown artifact: {artifact_id}") from error

    def read_json(self, artifact_id: str) -> Any:
        return json.loads(self.read_bytes(artifact_id))

    def read_array(self, artifact_id: str) -> np.ndarray:
        return np.load(io.BytesIO(self.read_bytes(artifact_id)), allow_pickle=False)

    def describe(self, artifact_id: str) -> dict:
        self._validate_id(artifact_id)
        with self._lock:
            if artifact_id in self._metadata:
                return dict(self._metadata[artifact_id])
        if self.root is None:
            raise KeyError(f"Unknown artifact: {artifact_id}")
        try:
            return json.loads((self.root / "artifacts" / f"{artifact_id}.json").read_text())
        except FileNotFoundError as error:
            raise KeyError(f"Unknown artifact: {artifact_id}") from error

    def relative_path(self, artifact_id: str) -> str:
        """Return the archive-relative filename without waiting for persistence."""
        ref = self.describe(artifact_id)
        if ref["media_type"].startswith("image/"):
            suffix = {
                "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp",
                "image/gif": ".gif", "image/bmp": ".bmp", "image/tiff": ".tiff",
                "image/svg+xml": ".svg", "image/avif": ".avif",
            }.get(ref["media_type"], ".img")
            return f"images/{ref['sha256']}{suffix}"
        return f"artifacts/{artifact_id}"

    def local_path(self, artifact_id: str) -> Path:
        """Wait for this artifact's durable file, without flushing other writes."""
        if self.root is None:
            raise ValueError("Memory-only artifacts have no local path")
        with self._ready:
            ref = self.describe(artifact_id)
            while ref["persistence"] == "pending":
                self._ready.wait()
                ref = self.describe(artifact_id)
            if ref["persistence"] == "failed":
                raise OSError(f"Artifact {artifact_id}: {ref['persistence_error']}")
        path = self.root / self.relative_path(artifact_id)
        if not path.is_file():
            raise FileNotFoundError(path)
        return path

    def status(self) -> dict:
        with self._lock:
            return {
                "artifacts": len(self._metadata),
                "memory_bytes": self._memory_bytes,
                "pending": sum(ref["persistence"] == "pending" for ref in self._metadata.values()),
                "failed": sum(ref["persistence"] == "failed" for ref in self._metadata.values()),
                "errors": {artifact_id: ref["persistence_error"]
                           for artifact_id, ref in self._metadata.items() if "persistence_error" in ref},
            }

    def close(self) -> None:
        if self._owns_writer:
            self._writer.close()

    def _persist(self, artifact_id: str, data: bytes) -> None:
        try:
            self._atomic_write(self.root / self.relative_path(artifact_id), data)
            ref = self.describe(artifact_id)
            ref["persistence"] = "persisted"
            self._atomic_write(self.root / "artifacts" / f"{artifact_id}.json", json.dumps(ref).encode())
            with self._lock:
                self._metadata[artifact_id] = ref
                cached = self._bytes.pop(artifact_id, None)
                if cached is not None:
                    self._memory_bytes -= len(cached)
                while self._memory_bytes > self._max_memory_bytes and self._bytes:
                    _, evicted = self._bytes.popitem(last=False)
                    self._memory_bytes -= len(evicted)
                self._ready.notify_all()
        except Exception as error:
            self._failed(artifact_id, error)
            raise

    def _failed(self, artifact_id: str, error: Exception) -> None:
        with self._lock:
            self._metadata[artifact_id].update(
                persistence="failed", persistence_error=f"{type(error).__name__}: {error}",
            )
            self._ready.notify_all()

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_bytes(data)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _validate_id(value: str) -> None:
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise ValueError("Invalid artifact ID")
