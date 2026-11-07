"""Actor proposal, review and execution archives; no physical execution policy."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np


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
            if proposal is not None:
                self.directory.mkdir(parents=True, exist_ok=True)
                np.savez_compressed(path, raw_actions=proposal.raw_actions, scoped_actions=proposal.scoped_actions)
            path = self.directory / f"{index:02d}.json"
            archived = {"schema": "hybrid.v2", **record, "review_images": self._relative_images(record["review_images"])}
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

    def write_summary(self, summary):
        path = self.directory / "summary.json"
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
        except Exception as error:
            self._record_error(path, error)
