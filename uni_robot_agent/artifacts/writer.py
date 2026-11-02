"""Background persistence queue that does not block tool execution."""

from __future__ import annotations

import queue
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from uni_robot_agent.telemetry import NullTracer


class PersistenceQueueFull(RuntimeError):
    """Optional data exceeded capacity; report the loss instead of writing synchronously."""


@dataclass(frozen=True)
class _WriteRequest:
    request_id: str
    name: str
    write: Callable[[], None]
    estimated_bytes: int
    queued_ns: int


class AsyncPersistence:
    """Bounded single-thread writer; submission transfers ownership, close waits for writes."""

    def __init__(
        self,
        *,
        name: str = "persistence",
        max_pending: int = 128,
        max_pending_bytes: int = 1 << 30,
        tracer=None,
    ) -> None:
        self._name = name
        self._max_pending = max(1, int(max_pending))
        self._max_pending_bytes = max(1, int(max_pending_bytes))
        self._tracer = tracer or NullTracer()
        self._queue: queue.Queue[_WriteRequest | None] = queue.Queue()
        self._lock = threading.Lock()
        self._pending = 0
        self._pending_bytes = 0
        self._written = 0
        self._failures: list[dict[str, str]] = []
        self._accepting = True
        self._worker = threading.Thread(
            target=self._run,
            name=f"{name}-writer",
            daemon=True,
        )
        self._worker.start()

    def submit(
        self,
        name: str,
        write: Callable[[], None],
        *,
        estimated_bytes: int = 0,
    ) -> str:
        size = max(0, int(estimated_bytes))
        with self._lock:
            if not self._accepting:
                raise RuntimeError(f"{self._name} writer is closed")
            if (
                self._pending >= self._max_pending
                or self._pending_bytes + size > self._max_pending_bytes
            ):
                raise PersistenceQueueFull(
                    f"{self._name} queue is full: "
                    f"pending={self._pending}, bytes={self._pending_bytes}"
                )
            request_id = uuid.uuid4().hex[:12]
            self._pending += 1
            self._pending_bytes += size
            # Enqueue under the same lock: otherwise close() could insert its sentinel
            # after the count increases but before the request, stranding that request.
            self._queue.put(_WriteRequest(
                request_id=request_id,
                name=name,
                write=write,
                estimated_bytes=size,
                queued_ns=time.monotonic_ns(),
            ))
        return request_id

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "pending": self._pending,
                "pending_bytes": self._pending_bytes,
                "written": self._written,
                "failures": list(self._failures[-10:]),
                "accepting": self._accepting,
            }

    def flush(self) -> None:
        self._queue.join()

    def close(self) -> None:
        with self._lock:
            if not self._accepting:
                return
            self._accepting = False
        # close() is the persistence boundary; ordinary tools and finish must not call it.
        self.flush()
        self._queue.put(None)
        self._worker.join()

    def _run(self) -> None:
        while True:
            request = self._queue.get()
            try:
                if request is None:
                    return
                self._write(request)
            finally:
                self._queue.task_done()

    def _write(self, request: _WriteRequest) -> None:
        started_ns = time.monotonic_ns()
        status = "ok"
        error: str | None = None
        try:
            request.write()
        except Exception as exc:  # noqa: BLE001 - record every persistence failure.
            status = "error"
            error = f"{type(exc).__name__}: {exc}"
        finally:
            with self._lock:
                self._pending -= 1
                self._pending_bytes -= request.estimated_bytes
                if error is None:
                    self._written += 1
                else:
                    self._failures.append({
                        "request_id": request.request_id,
                        "name": request.name,
                        "error": error,
                    })
            if self._tracer.enabled:
                self._tracer.record_duration(
                    "persistence.write",
                    time.monotonic_ns() - started_ns,
                    attributes={
                        "request_id": request.request_id,
                        "name": request.name,
                        "queue_wait_ms": (started_ns - request.queued_ns) / 1e6,
                        "estimated_bytes": request.estimated_bytes,
                    },
                    status=status,
                )
