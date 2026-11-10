"""Shared episode lifecycle, execution, publication, and archival.

The motion lock belongs to one episode. Snapshot reads never acquire it and
never enter a simulator's owner queue, so observers can read during motion.
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from uni_robot_agent.services.environment.contracts import (
    GoalRegionRequest,
    GoalRegionReply,
    ActionReply,
    CaptureRequest,
    EpisodeInfo,
    FinishRequest,
    PreviewRequest,
    RequestIdentity,
    RequestRejected,
    StepRequest,
)
from uni_robot_agent.services.environment.primitive import PrimitiveExecutor
from uni_robot_agent.services.environment.snapshots import SnapshotStore

from .backend import EnvironmentBackend


class ServiceError(RuntimeError):
    def __init__(self, code: str, message: str, *, status: int = 409,
                 execution_status: str = "not_started", details: dict | None = None):
        super().__init__(message)
        self.code, self.status = code, status
        self.execution_status = execution_status
        self.details = details or {}


class EnvironmentService:
    """Expose one prepared native episode through the environment protocol.

    Gripper steps and opt-in device segments forward low-level commands to
    native adapters. Model-level programs remain in tools. Real-time servo
    loops execute locally in a native controller, never as network ticks.
    """

    def __init__(self, backend: EnvironmentBackend, info: EpisodeInfo, artifacts, snapshots=None):
        self.backend, self.artifacts = backend, artifacts
        self._info = info.model_copy(deep=True)
        # Publish integration-owned static resources before any host exposes episode info.
        # This also covers prepared/batched hosts that do not use the standalone server.
        metadata = self._info.environment.metadata
        if metadata.get("goal_region_search") and "goal_region_search" not in self._info.capabilities:
            self._info.capabilities.append("goal_region_search")
        payloads = metadata.pop("artifact_payloads", {})
        if payloads:
            metadata.setdefault("artifacts", {}).update({
                name: artifacts.put_json(payload) for name, payload in payloads.items()
            })
        texts = metadata.pop("artifact_texts", {})
        if texts:
            metadata.setdefault("artifacts", {}).update({
                name: artifacts.put_bytes(item["text"].encode(), item["media_type"])
                for name, item in texts.items()
            })
        if metadata.get("robot_resources"):
            metadata["robot_resources"] = [
                {**item, "artifact": metadata["artifacts"][item["name"]]}
                for item in metadata["robot_resources"]
            ]
        self.snapshots = snapshots or SnapshotStore(artifacts)
        self._motion_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._requests: dict[str, dict[str, Any]] = {}
        self._next_sequence = 1
        self._lifecycle = "active"
        self._outcome: dict | None = None
        self._closed = False
        self._last_published_step = -1
        self._current_state_version = 0
        self._publication_error: str | None = None
        from .interrupts import Interrupts
        self.interrupts = Interrupts()
        self.primitive = PrimitiveExecutor(backend, on_step=self._on_step)
    def initialize(self) -> dict:
        """Publish the state prepared by the host; connecting clients never reset it."""
        with self._motion_lock:
            return self._capture(record=True)

    def info(self) -> EpisodeInfo:
        with self._state_lock:
            return self._info.model_copy(update={
                "lifecycle": self._lifecycle, "next_sequence": self._next_sequence,
            }, deep=True)

    def latest_snapshot(self) -> dict:
        result = self.snapshots.latest()
        with self._state_lock:
            return dict(result, source_current_state_version=self._current_state_version,
                        publication_error=self._publication_error)

    def snapshot(self, snapshot_id: str) -> dict:
        return self.snapshots.get(snapshot_id)

    def record(self, index: int = -1) -> dict:
        return self.snapshots.record(index)

    def request_status(self, request_id: str) -> dict:
        with self._state_lock:
            if request_id not in self._requests:
                raise KeyError(request_id)
            return dict(self._requests[request_id])

    def outcome(self) -> dict:
        with self._state_lock:
            if self._outcome is None:
                raise ServiceError("not_finished", "The episode has not been finalized")
            return dict(self._outcome)

    def step(self, identity: RequestIdentity, request: StepRequest) -> dict:
        """Execute one low-level command after checking its state version.

        Keeping the version check beside the physical step prevents a tool from
        applying a command planned against an observation that is already stale.
        The tool layer owns any multi-step loop or stopping policy.
        """
        def execute() -> dict:
            current = self.backend.read_state()
            if current.state_version != request.expected_state_version:
                raise RequestRejected("Stale action proposal; capture a fresh observation")
            return self.primitive.step(request.command)

        return self._run(identity, execute)

    def _require_devices(self):
        from .device_contracts import DeviceEpisodeInfo
        if not isinstance(self._info, DeviceEpisodeInfo):
            raise RequestRejected("This episode does not declare the device protocol")

    def device_state(self):
        self._require_devices()
        return self.backend.read_state().model_dump(mode="json")

    def interrupt(self, request):
        self._require_devices()
        if request.episode_id != self._info.episode_id:
            raise RequestRejected("Interrupt episode mismatch")
        with self._state_lock:
            if self._closed:
                raise RequestRejected("Device episode is closed")
        return self.interrupts.execute(request, self.backend)

    def device_preview(self, identity, request):
        self._require_devices()
        with self._motion_lock:
            self._begin(identity)
            try:
                if self.backend.read_state().state_version != request.expected_state_version:
                    raise RequestRejected("Preview observation is stale")
                result = self.backend.preview_device(request.command)
            except Exception as error:
                self._fail(identity, error, "not_started")
                raise
            self._complete(identity, result)
            return result

    def device_step(self, identity, request):
        from .device_contracts import DeviceReply
        self._require_devices()
        with self._motion_lock:
            self._begin(identity)
            try:
                before = self.backend.read_state()
                if before.state_version != request.expected_state_version:
                    raise RequestRejected("Stale device proposal; read fresh state")
                self.backend.validate_device(request.command)
            except Exception as error:
                self._fail(identity, error, "not_started")
                raise
            cancellation = self.interrupts.begin(identity, request.command.device)
            try:
                feedback = self.backend.execute_device(request.command, cancellation=cancellation)
                after = self.backend.read_state()
            except Exception as error:
                self._fail(identity, error, "unknown")
                raise ServiceError("execution_unknown", "Device command needs reconciliation",
                                   status=500, execution_status="unknown") from error
            finally:
                self.interrupts.end()
            status = feedback.get("execution_status", "completed")
            if status not in {"completed", "partial", "rejected", "unknown"}:
                raise RuntimeError("Invalid native execution status")
            snapshot, publication_error = None, None
            try:
                snapshot = self._capture(record=True)
            except Exception as error:
                publication_error = f"{type(error).__name__}: {error}"
            trace, archive_error = self._archive({"identity": identity.model_dump(),
                "state_before": before.model_dump(), "state_after": after.model_dump(), "feedback": feedback})
            result = DeviceReply(identity=identity, state_before=before, state_after=after,
                feedback=feedback, snapshot=snapshot, trace=trace, execution_status=status,
                artifact_error=archive_error or publication_error).model_dump(mode="json")
            if status == "unknown":
                self._fail(identity, RuntimeError(feedback.get("error", "Unknown device completion")), "unknown")
                raise ServiceError("execution_unknown", "Device completion is unknown", execution_status="unknown", details=feedback)
            self._complete(identity, result)
            return result

    def capture(self, identity: RequestIdentity, request: CaptureRequest) -> dict:
        with self._motion_lock:
            self._begin(identity)
            try:
                result = self._capture(request, record=True)
            except Exception as error:
                self._fail(identity, error, "not_started")
                if isinstance(error, (RequestRejected, ServiceError)):
                    raise
                raise ServiceError("capture_failed", "Image capture failed; no physical action was executed",
                                   status=500, execution_status="not_started") from error
            self._complete(identity, result)
            return result

    def preview(self, identity: RequestIdentity, request: PreviewRequest) -> dict:
        with self._motion_lock:
            self._begin(identity)
            try:
                if self.backend.read_state().state_version != request.expected_state_version:
                    raise RequestRejected("Preview observation is stale")
                result = self.backend.preview_joints(request.commands)
            except Exception as error:
                self._fail(identity, error, "not_started")
                raise
            self._complete(identity, result)
            return result

    def solve_goal_region(self, identity: RequestIdentity, request: GoalRegionRequest) -> dict:
        with self._motion_lock:
            self._begin(identity)
            try:
                if "goal_region_search" not in self._info.capabilities:
                    raise RequestRejected("Environment does not support goal-region search")
                if self.backend.read_state().state_version != request.expected_state_version:
                    raise RequestRejected("Goal-region observation is stale")
                result = GoalRegionReply.model_validate(self.backend.solve_goal_region(request))
                if result.state_version != request.expected_state_version:
                    raise RequestRejected("Goal-region result is stale")
                result = result.model_dump(mode="json")
            except Exception as error:
                self._fail(identity, error, "not_started")
                raise
            self._complete(identity, result)
            return result

    def finish(self, identity: RequestIdentity, request: FinishRequest) -> dict:
        with self._motion_lock:
            self._begin(identity, finishing=True)
            with self._state_lock:
                self._lifecycle = "finishing"
            try:
                outcome = self.backend.finish(request.reason, request.status)
                artifact, artifact_error = self._archive(outcome)
                result = {"outcome": outcome, "artifact": artifact, "artifact_error": artifact_error}
                # Closing the native handle releases only this episode's room.
                self.backend.close()
                self._closed = True
                with self._state_lock:
                    self._outcome = result
                    self._lifecycle = "finished"
            except Exception as error:
                with self._state_lock:
                    self._lifecycle = "failed"
                self._fail(identity, error, "unknown")
                raise
            self._complete(identity, result)
            return result

    def close(self, reason: str = "server_shutdown") -> None:
        """Host shutdown waits for the episode's current action before releasing it."""
        with self._motion_lock:
            if not self._closed:
                try:
                    if self._outcome is None:
                        outcome = self.backend.finish(reason, None)
                        ref, error = self._archive(outcome)
                        self._outcome = {"outcome": outcome, "artifact": ref, "artifact_error": error}
                finally:
                    self.backend.close()
                    self._closed = True
                    self._lifecycle = "finished"
            self.artifacts.close()

    def _begin(self, identity: RequestIdentity, *, finishing: bool = False) -> None:
        with self._state_lock:
            if identity.episode_id != self._info.episode_id:
                raise ServiceError("episode_mismatch", "The URL belongs to another episode")
            if identity.request_id in self._requests:
                raise ServiceError("duplicate_request", "Read the existing request result; do not replay it")
            if identity.sequence != self._next_sequence:
                raise ServiceError("sequence_mismatch", "Unexpected request sequence",
                                   details={"next_sequence": self._next_sequence})
            if self._lifecycle != "active" and not (finishing and self._lifecycle == "failed"):
                raise ServiceError("episode_closed", "The episode no longer accepts requests")
            self._next_sequence += 1
            self._requests[identity.request_id] = {"status": "running", "identity": identity.model_dump()}

    def _run(self, identity: RequestIdentity, execute: Callable[[], dict]) -> dict:
        with self._motion_lock:
            self._begin(identity)
            try:
                before = self.backend.read_state()
            except Exception as error:
                self._fail(identity, error, "unknown")
                raise ServiceError("state_unavailable", "Native state could not be read",
                                   status=500, execution_status="unknown") from error
            try:
                feedback = execute()
                after = self.backend.read_state()
            except RequestRejected as error:
                after = self.backend.read_state()
                if after.native_steps == before.native_steps:
                    self._fail(identity, error, "not_started")
                    raise
                feedback = {"stop_reason": "planning_rejected", "error": str(error),
                            "steps_executed": after.native_steps - before.native_steps}
            except Exception as error:
                # A native exception can follow a successful physics step. Do not
                # infer zero execution from the absence of an acknowledgement.
                self._fail(identity, error, "unknown")
                raise ServiceError("execution_unknown", "Native execution did not return a complete receipt",
                                   status=500, execution_status="unknown") from error
            snapshot = None
            publication_error = None
            try:
                snapshot = self._capture(record=True)
            except Exception as error:  # noqa: BLE001 - preserve the physical receipt if publication fails.
                publication_error = f"{type(error).__name__}: {error}"
            trace, archive_error = self._archive({
                "identity": identity.model_dump(), "state_before": before.model_dump(),
                "state_after": after.model_dump(), "feedback": feedback,
            })
            reason = feedback.get("stop_reason")
            status = "partial" if reason in {"planning_rejected", "unsafe_motion", "tracking_interrupted"} else "completed"
            if not feedback.get("accepted", True):
                status = "rejected"
            if reason == "planning_rejected" and after.native_steps == before.native_steps:
                status = "rejected"
            result = ActionReply(
                identity=identity, state_before=before, state_after=after,
                feedback=feedback, snapshot=snapshot, trace=trace,
                artifact_error=archive_error or publication_error,
                execution_status=status,
            ).model_dump(mode="json")
            self._complete(identity, result)
            return result

    def _capture(self, request: CaptureRequest | None = None, *, record: bool = False) -> dict:
        request = request or CaptureRequest(high_resolution=False)
        captured = self.backend.capture(request.cameras, high_resolution=request.high_resolution)
        # Identity is owned by the bound service, not inferred from room numbers.
        captured = replace(captured, episode_id=self._info.episode_id)
        result = self.snapshots.publish(captured, record=record)
        if "native_steps" in captured.state:
            self._last_published_step = captured.state["native_steps"]
        with self._state_lock:
            self._current_state_version = captured.state_version
        return result

    def _on_step(self, state) -> None:
        with self._state_lock:
            self._current_state_version = state.state_version
        interval = self._info.environment.observation_interval_steps
        if state.native_steps - self._last_published_step < interval:
            return
        try:
            self._capture(record=False)
            with self._state_lock:
                self._publication_error = None
        except Exception as error:  # noqa: BLE001 - acknowledged steps remain authoritative.
            # Observation failure must not erase acknowledged physical execution.
            with self._state_lock:
                self._publication_error = f"{type(error).__name__}: {error}"

    def _archive(self, data: dict) -> tuple[dict | None, str | None]:
        try:
            return self.artifacts.put_json(data), None
        except Exception as error:  # noqa: BLE001 - report archival failure alongside the action.
            return None, f"{type(error).__name__}: {error}"

    def _complete(self, identity: RequestIdentity, result: dict) -> None:
        with self._state_lock:
            self._requests[identity.request_id].update(status="completed", result=result)

    def _fail(self, identity: RequestIdentity, error: Exception, status: str) -> None:
        with self._state_lock:
            self._requests[identity.request_id].update(
                status="failed", error=str(error), execution_status=status,
            )
            if status == "unknown":
                self._lifecycle = "failed"
