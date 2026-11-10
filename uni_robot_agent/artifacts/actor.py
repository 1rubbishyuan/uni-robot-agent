"""Actor proposal, review and execution archives; no physical execution policy."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from uni_robot_agent.artifacts.run import write_json_atomic


class ActorArchive:
    def __init__(self, output: Path, session_id: str, storage_errors: dict[str, str]):
        self.output = output.resolve()
        self.directory = self.output / "actor_sessions" / session_id
        self.storage_errors = storage_errors
        self._errors: dict[str, str] = {}
        self.error: str | None = None

    @property
    def artifact_error(self) -> str | None:
        return "; ".join(f"{path}: {error}" for path, error in self._errors.items()) or None

    def _record_error(self, path: Path, error: Exception) -> None:
        name = str(path.relative_to(self.output))
        message = f"{type(error).__name__}: {error}"
        self._errors[name] = self.storage_errors[name] = message

    def _relative_images(self, images):
        return {name: str(Path(path).relative_to(self.output)) for name, path in images.items()}

    def write_chunk(self, index, record, *, proposal=None):
        path = self.directory / f"{index:02d}.npz"
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            if proposal is not None and record.get("prediction", {}).get("kind", "numeric") == "numeric":
                raw, scoped = np.asarray(proposal.raw_actions, dtype=float), np.asarray(proposal.scoped_actions, dtype=float)
                if not np.isfinite(raw).all() or not np.isfinite(scoped).all():
                    raise ValueError("Archived numeric actions must be finite")
                np.savez_compressed(path, raw_actions=raw, scoped_actions=scoped)
            path = self.directory / f"{index:02d}.json"
            archived = {"schema": "hybrid.v3", **record, "review_images": self._relative_images(record["review_images"])}
            previous = record["context"]["previous_actor"]
            if previous is not None:
                archived["context"] = {**record["context"], "previous_actor": {
                    **previous, "images": self._relative_images(previous["images"]),
                }}
            path.write_text(json.dumps(archived, indent=2, allow_nan=False) + "\n")
        except Exception as error:
            self._record_error(path, error)
            self.error = f"Actor recording failed: {type(error).__name__}: {error}"
            raise

    def write_review(self, index, attempt, evidence):
        """Persist original provider bytes before a rejected review can be retried."""
        path = self.directory / f"{index:02d}.review-{attempt:02d}.json"
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            evidence = dict(evidence)
            raw = evidence.pop("raw_response", None)
            if raw is not None:
                response_path = path.with_suffix(".response")
                response_path.write_bytes(raw)
                evidence["response"] = {"path": response_path.name,
                    "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}
            write_json_atomic(path, {"schema": "hybrid.review.v1", "session_id": self.directory.name,
                "chunk_index": index, "attempt": attempt, **evidence}, allow_nan=False, trailing_newline=True)
        except Exception as error:
            self._record_error(path, error)
            raise

    def write_summary(self, summary):
        path = self.directory / "summary.json"
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
        except Exception as error:  # noqa: BLE001 - preserve summary persistence diagnostics
            self._record_error(path, error)
