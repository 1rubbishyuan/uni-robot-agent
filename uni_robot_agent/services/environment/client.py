"""One typed client for a prepared episode URL, with no physical retries."""
from __future__ import annotations

import json
import threading
from urllib.parse import quote, urlsplit
from uuid import uuid4

from pydantic import ValidationError

from uni_robot_agent.artifacts.mirror import RemoteArtifactMirror
from uni_robot_agent.services.environment.contracts import (
    GoalRegionRequest,
    GoalRegionReply,
    ActionReply,
    CaptureRequest,
    EpisodeInfo,
    FinishRequest,
    PreviewRequest,
    ProtocolError,
    RequestEnvelope,
    RequestIdentity,
    StepRequest,
)
from uni_robot_agent.services.errors import HttpCallError
from uni_robot_agent.services.http import HttpTransport


class EnvironmentRequestError(RuntimeError):
    def __init__(self, error: ProtocolError):
        super().__init__(error.message)
        self.error = error
        self.execution_uncertain = error.execution_status == "unknown"


class ExecutionUncertain(RuntimeError):
    """A submitted request needs reconciliation; resending it is unsafe."""
    execution_uncertain = True


class EnvironmentClient:
    def __init__(self, episode_url: str, *, tracer=None, timeout_s: float = 450):
        parsed = urlsplit(episode_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError("episode_url must be an absolute HTTP(S) base URL without query or fragment")
        self.episode_url = episode_url.rstrip("/")
        self.timeout_s, self.tracer = timeout_s, tracer
        self._transport = HttpTransport(self.episode_url, timeout_s=timeout_s, tracer=tracer)
        self.info: EpisodeInfo | None = None
        self._next_sequence = 1
        self._write_lock = threading.RLock()
        self.uncertain = False
        self._pending_identity: RequestIdentity | None = None
        self._pending_interrupt = None
        self._interrupt_lock = threading.RLock()
        self._closed = False
        self._artifact_mirror: RemoteArtifactMirror | None = None

    def attach_artifact_store(self, store) -> RemoteArtifactMirror:
        """Attach the shared store used to mirror remote artifact refs.

        The service remains authoritative for snapshot IDs and action ordering;
        this handle only makes the first artifact response durable locally so
        later consumers can use paths without another HTTP read.
        """
        self._artifact_mirror = RemoteArtifactMirror(self, store)
        return self._artifact_mirror

    @property
    def artifact_mirror(self) -> RemoteArtifactMirror | None:
        return self._artifact_mirror

    @property
    def pending_request_id(self) -> str | None:
        return self._pending_identity.request_id if self._pending_identity else None

    def connect(self) -> EpisodeInfo:
        # Refreshing the sequence must not race a write's acknowledgement.
        with self._write_lock:
            from .device_contracts import parse_info
            result = parse_info(self._get("info"))
            if self.info is not None and self.info.episode_id != result.episode_id:
                raise ExecutionUncertain("The episode URL was rebound to another episode")
            self.info = result
            self._next_sequence = result.next_sequence
            return result

    def step(self, request: StepRequest) -> ActionReply:
        """Execute one primitive command through the environment protocol.

        Tool code uses this method to implement model-level actions.  Keeping
        the call here also makes the one-shot request boundary explicit: the
        service never receives a ``move`` or ``grasp`` intent.
        """
        return self._post("steps", request, reply_type=ActionReply)

    def device_step(self, request):
        from .device_contracts import DeviceReply
        return self._post("device-steps", request, reply_type=DeviceReply)

    def device_preview(self, request):
        return self._post("device-previews", request)

    def device_state(self):
        from .device_contracts import DeviceState
        return DeviceState.model_validate(self._get("device-state"))

    def interrupt_device(self, device, *, target_request_id=None, request_id=None):
        with self._interrupt_lock:
            return self._interrupt_device(device, target_request_id=target_request_id, request_id=request_id)

    def _interrupt_device(self, device, *, target_request_id=None, request_id=None):
        # Independent identity and transport path; the action writer may be blocked.
        from .device_contracts import InterruptRequest
        if self.info is None:
            raise RuntimeError("connect() must precede interrupts")
        request = InterruptRequest(episode_id=self.info.episode_id, request_id=request_id or uuid4().hex,
                                   device=device, target_request_id=target_request_id)
        if self._pending_interrupt is not None:
            raise ExecutionUncertain("Reconcile the pending interrupt before sending another stop")
        self._pending_interrupt = request
        try:
            result = json.loads(self._request("POST", "interrupts", request.model_dump_json().encode()))
            if result["request_id"] != request.request_id or result["status"] != "completed":
                raise ValueError("Interrupt completion is unknown")
            self._pending_interrupt = None
            return result
        except EnvironmentRequestError as error:
            if not error.execution_uncertain:
                self._pending_interrupt = None
                raise
            self.uncertain = True
            raise ExecutionUncertain(f"Interrupt {request.request_id} requires receipt inspection") from error
        except Exception as error:
            self.uncertain = True
            raise ExecutionUncertain(f"Interrupt {request.request_id} requires receipt inspection") from error

    def interrupt_status(self, request_id):
        return self._get("interrupts/" + quote(request_id, safe=""))

    def preview(self, request: PreviewRequest) -> dict:
        return self._post("previews", request)

    def solve_goal_region(self, request: GoalRegionRequest) -> GoalRegionReply:
        return GoalRegionReply.model_validate(self._post("goal-regions", request))

    def capture(self, request: CaptureRequest) -> dict:
        return self._post("captures", request)

    def latest_snapshot(self) -> dict:
        return self._get("snapshots/latest")

    def snapshot(self, snapshot_id: str) -> dict:
        return self._get("snapshots/" + quote(snapshot_id, safe=""))

    def record(self, index: int = -1) -> dict:
        if type(index) is not int or index < -1:
            raise ValueError("record index must be -1 or nonnegative")
        return self._get(f"records?index={index}")

    def artifact_bytes(self, artifact_id: str) -> bytes:
        return self._request("GET", "artifacts/" + quote(artifact_id, safe=""))

    def finish(self, request: FinishRequest) -> dict:
        return self._post("finish", request)

    def outcome(self) -> dict:
        return self._get("outcome")

    def request_status(self, request_id: str) -> dict:
        return self._get("requests/" + quote(request_id, safe=""))

    def reconcile(self) -> dict | None:
        """Read an existing receipt after timeout, without submitting any action."""
        with self._write_lock:
            if self._pending_interrupt is not None:
                result = self.interrupt_status(self._pending_interrupt.request_id)
                if result.get("request") != self._pending_interrupt.model_dump(mode="json"):
                    raise ExecutionUncertain("Interrupt receipt identity does not match")
                if result.get("status") != "completed":
                    return result
                self._pending_interrupt = None
                self.uncertain = self._pending_identity is not None
            if self._pending_identity is None:
                return None
            result = self.request_status(self._pending_identity.request_id)
            try:
                if RequestIdentity.model_validate(result["identity"]) != self._pending_identity:
                    raise ValueError("Receipt identity does not match the submitted request")
                if result["status"] not in {"running", "completed", "failed"}:
                    raise ValueError("Unknown receipt status")
                if result["status"] == "failed" and result.get("execution_status") not in {
                    "not_started", "partial", "unknown",
                }:
                    raise ValueError("Failed receipt does not establish execution status")
            except (KeyError, TypeError, ValueError) as error:
                self.uncertain = True
                raise ExecutionUncertain("Episode receipt is invalid; request cannot be replayed") from error
            if result["status"] == "running" or result.get("execution_status") == "unknown":
                return result
            self.connect()
            self.uncertain = False
            self._pending_identity = None
            return result

    def close(self) -> None:
        # The session explicitly finalizes the episode. Closing a client handle
        # alone must never release a room owned by another process.
        self._closed = True
        self._transport.close()

    def _post(self, path: str, payload, *, reply_type=None):
        from uni_robot_agent.execution import current_execution_id

        with self._write_lock:
            if self._closed:
                raise RuntimeError("Environment client is closed")
            if self.uncertain:
                raise ExecutionUncertain("Read the pending request result before submitting another request")
            if self.info is None:
                raise RuntimeError("connect() must precede episode requests")
            identity = RequestIdentity(
                episode_id=self.info.episode_id, request_id=uuid4().hex,
                sequence=self._next_sequence, execution_id=current_execution_id(),
            )
            body = RequestEnvelope(identity=identity, payload=payload.model_dump(mode="json")).model_dump_json().encode()
            self._pending_identity = identity
            try:
                result = json.loads(self._request("POST", path, body))
                if reply_type is not None:
                    result = reply_type.model_validate(result)
                    if result.identity != identity:
                        raise ValueError("Response identity does not match the submitted request")
                elif not isinstance(result, dict):
                    raise ValueError("Episode response must be an object")
            except EnvironmentRequestError as error:
                self._next_sequence = error.error.details.get("next_sequence", self._next_sequence)
                self.uncertain = error.execution_uncertain
                if not self.uncertain:
                    self._pending_identity = None
                raise
            except Exception as error:
                self.uncertain = True
                raise ExecutionUncertain(
                    f"Request {identity.request_id} may have executed; query its receipt before continuing"
                ) from error
            self._next_sequence += 1
            self._pending_identity = None
            return result

    def _get(self, path: str) -> dict:
        # The pooled transport supports simultaneous reads and writes. Observation
        # reads never wait on the client's mutation lock.
        return json.loads(self._request("GET", path))

    def _request(self, method: str, path: str, body: bytes | None = None) -> bytes:
        if self._closed:
            raise RuntimeError("Environment client is closed")
        headers = {"Content-Type": "application/json"}
        if self.info is not None:
            headers["X-Episode-ID"] = self.info.episode_id
        try:
            return self._transport.request(method, path, content=body, headers=headers,
                                           timeout_s=self.timeout_s).content
        except HttpCallError as error:
            try:
                payload = json.loads(error.content)
                failure = ProtocolError.model_validate(payload["error"])
            except (ValueError, KeyError, ValidationError):
                failure = ProtocolError(code="http_error", message=f"Episode HTTP error {error.status}",
                                        execution_status="unknown" if method == "POST" else "not_started")
            raise EnvironmentRequestError(failure) from error
