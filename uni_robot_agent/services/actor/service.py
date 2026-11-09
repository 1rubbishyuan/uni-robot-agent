"""Actor sessions, admission, receipts, and one model batching queue."""

import math
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass, field
from uuid import uuid4

from uni_robot_agent.services.batching import BatchQueueFull, BatchWorker, BatchWorkerClosed
from uni_robot_agent.services.errors import ServiceError
from uni_robot_agent.telemetry.logging import get_logger

from .backend import ActorBackend, ActorWork
from .contracts import (CreateSession, PredictReply, PredictRequest, Receipt, SessionInfo,
                        validate_observation, validate_prediction)


@dataclass
class _Session:
    state: object
    touched: float
    sequence: int = 0
    status: str = "ready"
    seen: set[str] = field(default_factory=set)
    receipts: dict[str, tuple[float, Receipt]] = field(default_factory=dict)


logger = get_logger("actor_service")


class ActorService:
    def __init__(
        self,
        backend: ActorBackend,
        *,
        max_batch_size=2,
        batch_wait_ms=5,
        max_queue_size=64,
        max_sessions=1024,
        idle_timeout_s=1800,
        receipt_ttl_s=3600,
        max_receipts=128,
        max_session_requests=4096,
    ):
        if not all(math.isfinite(value) for value in (idle_timeout_s, receipt_ttl_s)):
            raise ValueError("Session retention periods must be finite")
        if (
            min(
                max_sessions,
                idle_timeout_s,
                receipt_ttl_s,
                max_receipts,
                max_session_requests,
            )
            <= 0
        ):
            raise ValueError("Session limits and retention periods must be positive")
        self.backend, self.info = backend, backend.info
        self.max_sessions, self.idle_timeout_s = max_sessions, idle_timeout_s
        self.receipt_ttl_s, self.max_receipts = receipt_ttl_s, max_receipts
        self.max_session_requests = max_session_requests
        self._lock = threading.RLock()
        self._sessions: dict[str, _Session] = {}
        self._closed = False
        self.worker = BatchWorker(
            backend.predict_batch,
            max_batch_size=max_batch_size,
            max_wait_ms=batch_wait_ms,
            max_queue_size=max_queue_size,
            name="actor-model",
        )

    def _error(self, code, message, *, status=409):
        return ServiceError(code, message, status=status, details={"accepted": False})

    def _prune(self):
        now = time.monotonic()
        for session_id, session in list(self._sessions.items()):
            if (
                session.status not in {"running", "closing"}
                and now - session.touched > self.idle_timeout_s
            ):
                if session.status != "closed":
                    self.backend.close_session(session.state)
                del self._sessions[session_id]
                continue
            session.receipts = {
                key: value
                for key, value in session.receipts.items()
                if value[1].status == "running" or now - value[0] <= self.receipt_ttl_s
            }

    def create_session(self, request: CreateSession) -> SessionInfo:
        with self._lock:
            self._prune()
            if self._closed:
                raise self._error(
                    "service_closed", "Actor service is closed", status=503
                )
            if (
                request.model_id != self.info.model_id
                or request.profile != self.info.profile.name
            ):
                raise self._error(
                    "model_mismatch",
                    "Actor model or profile differs from the requested model",
                )
            # Closed records retain receipts without reserving model capacity.
            if sum(session.status != "closed" for session in self._sessions.values()) >= self.max_sessions:
                raise self._error(
                    "session_limit", "Actor session capacity reached", status=503
                )
            identity = uuid4().hex
            session = _Session(self.backend.create_session(), time.monotonic())
            self._sessions[identity] = session
            return self._describe(identity, session)

    def _get(self, identity):
        self._prune()
        session = self._sessions.get(identity)
        if session is None:
            raise self._error(
                "session_missing",
                "Actor session expired or belongs to a previous process",
                status=410,
            )
        session.touched = time.monotonic()
        return session

    def _describe(self, identity, session):
        return SessionInfo(
            session_id=identity,
            model_id=self.info.model_id,
            next_sequence=session.sequence,
            status=session.status,
        )

    def get_session(self, identity) -> SessionInfo:
        with self._lock:
            return self._describe(identity, self._get(identity))

    def close_session(self, identity) -> SessionInfo:
        with self._lock:
            session = self._get(identity)
            if session.status in {"running", "closing"}:
                session.status = "closing"
            elif session.status != "closed":
                self.backend.close_session(session.state)
                session.state, session.status = None, "closed"
            return self._describe(identity, session)

    def receipt(self, identity, request_id) -> Receipt:
        with self._lock:
            session = self._get(identity)
            record = session.receipts.get(request_id)
            if record is None:
                raise self._error(
                    "receipt_missing",
                    "Actor request receipt is unavailable; do not replay",
                    status=404,
                )
            return record[1].model_copy(deep=True)

    def enqueue(self, request: PredictRequest) -> Future[PredictReply]:
        with self._lock:
            if self._closed:
                raise self._error(
                    "service_closed", "Actor service is closed", status=503
                )
            session = self._get(request.session_id)
            if request.request_id in session.seen:
                raise self._error(
                    "duplicate_request",
                    "Actor request was already accepted; query its receipt",
                )
            if session.status != "ready":
                raise self._error(
                    "session_unavailable", f"Actor session is {session.status}"
                )
            if request.sequence != session.sequence:
                raise self._error(
                    "sequence_mismatch", "Actor sequence does not match the session"
                )
            if len(session.seen) >= self.max_session_requests:
                raise self._error(
                    "session_limit",
                    "Actor session request limit reached; create a new session",
                )
            profile = self.info.profile
            try:
                validate_observation(request.observation, profile)
            except ValueError as exc:
                raise self._error("invalid_observation", str(exc), status=422) from exc
            try:
                model_future = self.worker.enqueue(
                    ActorWork(session.state, request.observation)
                )
            except (BatchQueueFull, BatchWorkerClosed) as exc:
                code = (
                    "queue_full"
                    if isinstance(exc, BatchQueueFull)
                    else "service_closed"
                )
                raise self._error(code, str(exc), status=503) from exc
            session.status = "running"
            session.seen.add(request.request_id)
            receipt = Receipt(
                session_id=request.session_id,
                request_id=request.request_id,
                sequence=request.sequence,
                status="running",
            )
            session.receipts[request.request_id] = (time.monotonic(), receipt)
            result: Future[PredictReply] = Future()
            result.set_running_or_notify_cancel()
            # Register only after the receipt exists; completed futures invoke callbacks immediately.
            model_future.add_done_callback(
                lambda future: self._complete(request, session, result, future)
            )
            return result

    def predict(self, request: PredictRequest) -> PredictReply:
        return self.enqueue(request).result()

    def _complete(self, request, session, result, model_future):
        try:
            prediction = model_future.result()
            reply = PredictReply(
                session_id=request.session_id,
                request_id=request.request_id,
                sequence=request.sequence,
                model_id=self.info.model_id,
                checkpoint_sha256=self.info.checkpoint_sha256,
                profile=self.info.profile.name,
                prediction=prediction,
            )
            validate_prediction(reply.prediction, self.info.profile)
        except BaseException as exc:  # noqa: BLE001 - always complete admitted requests.
            error = ServiceError(
                "inference_failed",
                str(exc),
                status=500,
                details={
                    "accepted": True,
                    "request_id": request.request_id,
                    "session_id": request.session_id,
                    "sequence": request.sequence,
                },
            )
            reply = None
        else:
            error = None
        with self._lock:
            closing = session.status == "closing"
            session.sequence += int(error is None)
            session.status = "poisoned" if error else "ready"
            session.touched = time.monotonic()
            session.receipts[request.request_id] = (
                session.touched,
                Receipt(
                    session_id=request.session_id,
                    request_id=request.request_id,
                    sequence=request.sequence,
                    status="failed" if error else "succeeded",
                    reply=reply,
                    error=str(error) if error else None,
                ),
            )
            while len(session.receipts) > self.max_receipts:
                del session.receipts[next(iter(session.receipts))]
            if closing:
                try:
                    self.backend.close_session(session.state)
                except Exception:
                    logger.exception(
                        "Actor session cleanup failed after in-flight inference"
                    )
                finally:
                    session.state, session.status = None, "closed"
        if error:
            result.set_exception(error)
        else:
            result.set_result(reply)

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self.worker.close()
        with self._lock:
            for session in self._sessions.values():
                if session.status != "closed":
                    self.backend.close_session(session.state)
                    session.state, session.status = None, "closed"
        self.backend.close()
