"""Mirror artifacts returned by a remote environment into uni-robot-agent storage.

The environment service owns snapshot identity and ordering. Perception,
actor bindings and prompt assembly share local copies, so each remote artifact
is downloaded once per mirror.
"""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path

from uni_robot_agent.artifacts.store import ArtifactStore


class RemoteArtifactMirror:
    """Materialize remote artifact references in a local ``ArtifactStore``.

    The client must expose ``artifact_bytes(remote_id)``. The first fetch
    checks any supplied size and hash; subsequent requests for that remote ID
    reuse the content-addressed local reference.
    """

    def __init__(self, client, store: ArtifactStore):
        self.client = client
        self.store = store
        self._refs: dict[str, dict] = {}

    def mirror_ref(self, reference: dict) -> dict:
        """Fetch one remote ref once and return its local artifact reference."""
        remote_id = _artifact_id(reference)
        cached = self._refs.get(remote_id)
        if cached is not None:
            return dict(cached)

        data = bytes(self.client.artifact_bytes(remote_id))
        expected_size = reference.get("size_bytes")
        if expected_size is not None and int(expected_size) != len(data):
            raise ValueError(f"Remote artifact size mismatch: {remote_id}")
        expected_sha256 = reference.get("sha256")
        if expected_sha256 and hashlib.sha256(data).hexdigest() != expected_sha256:
            raise ValueError(f"Remote artifact hash mismatch: {remote_id}")

        local = self.store.put_bytes(data, reference.get("media_type") or "application/octet-stream")
        # Local IDs include the media type and may differ from remote IDs.
        # Keep both identities: local storage reads use the returned artifact_id.
        result = {
            **local,
            "remote_artifact_id": remote_id,
            "remote_sha256": expected_sha256 or hashlib.sha256(data).hexdigest(),
        }
        self._refs[remote_id] = result
        return dict(result)

    def mirror_snapshot(self, snapshot: dict) -> dict:
        """Create a local manifest while retaining the remote snapshot identity.

        ``snapshot_id`` identifies the remote observation; ``local_snapshot_id``
        addresses its mirrored manifest in the local store. Localization consumes
        the mirrored frames, while remote artifact reads use the original IDs.
        """
        local = copy.deepcopy(snapshot)
        remote_snapshot_id = snapshot.get("snapshot_id")
        cameras = local.get("cameras", {})
        for frame in cameras.values():
            if frame.get("rgb") is not None:
                frame["rgb"] = self.mirror_ref(frame["rgb"])
            if frame.get("depth_m") is not None:
                frame["depth_m"] = self.mirror_ref(frame["depth_m"])
        # Exclude mirror-only metadata from the stored manifest so equivalent
        # source and local snapshots keep the same content-addressed identity.
        manifest_for_storage = copy.deepcopy(local)
        for frame in manifest_for_storage.get("cameras", {}).values():
            for key in ("rgb", "depth_m"):
                if frame.get(key) is not None:
                    frame[key] = {
                        name: value for name, value in frame[key].items()
                        if name not in {"remote_artifact_id", "remote_sha256"}
                    }
        manifest_ref = self.store.put_json({
            key: value for key, value in manifest_for_storage.items()
            if key not in {"snapshot_id", "manifest", "remote_snapshot_id"}
        })
        local["local_snapshot_id"] = manifest_ref["artifact_id"]
        local["manifest"] = manifest_ref
        if remote_snapshot_id is not None:
            local["remote_snapshot_id"] = remote_snapshot_id
        return local

    def local_path(self, reference: dict) -> Path:
        """Return the verified file owned by the shared artifact store."""
        local = self.mirror_ref(reference)
        return self.store.local_path(local["artifact_id"])


def _artifact_id(reference: dict) -> str:
    # A mirrored frame contains both identities.  Always address the remote
    # service with its original ID; the local content-addressed ID is only for
    # reads from ``ArtifactStore``.
    value = None
    if isinstance(reference, dict):
        value = reference.get("remote_artifact_id") or reference.get("artifact_id")
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError("Invalid remote artifact ID")
    return value


__all__ = ["RemoteArtifactMirror"]
