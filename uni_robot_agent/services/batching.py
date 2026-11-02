"""Small bounded micro-batch worker shared by model services."""

from __future__ import annotations

import math
import queue
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Generic, TypeVar

RequestT = TypeVar("RequestT")
ResponseT = TypeVar("ResponseT")


@dataclass(frozen=True)
class BatchStats:
    total_requests: int
    total_batches: int
    max_observed_batch_size: int


@dataclass
class _Pending(Generic[RequestT, ResponseT]):
    request: RequestT
    future: Future[ResponseT]


class BatchQueueFull(RuntimeError):
    pass


class BatchWorkerClosed(RuntimeError):
    pass


class BatchWorker(Generic[RequestT, ResponseT]):
    """Collect nearby requests and execute them on one worker thread."""

    def __init__(
        self,
        process_batch: Callable[
            [Sequence[RequestT]], Sequence[ResponseT | BaseException]
        ],
        *,
        max_batch_size: int,
        max_wait_ms: float,
        max_queue_size: int,
        name: str,
    ) -> None:
        if type(max_batch_size) is not int or max_batch_size < 1:
            raise ValueError("max_batch_size must be positive")
        if not math.isfinite(max_wait_ms) or max_wait_ms < 0:
            raise ValueError("max_wait_ms must be finite and nonnegative")
        if type(max_queue_size) is not int or max_queue_size < max_batch_size:
            raise ValueError("max_queue_size must be at least max_batch_size")
        self._process_batch = process_batch
        self._max_batch_size = max_batch_size
        self._max_wait_s = max_wait_ms / 1000.0
        self._queue: queue.Queue[object] = queue.Queue(maxsize=max_queue_size)
        self._stop = object()
        self._state_lock = threading.Lock()
        self._closed = False
        self._total_requests = 0
        self._total_batches = 0
        self._max_observed_batch_size = 0
        self._thread = threading.Thread(
            target=self._run,
            name=name,
            daemon=True,
        )
        self._thread.start()

    def submit(self, request: RequestT) -> ResponseT:
        return self.enqueue(request).result()

    def enqueue(self, request: RequestT) -> Future[ResponseT]:
        """Admit work without blocking an HTTP event loop or growing an executor queue."""
        future: Future[ResponseT] = Future()
        pending = _Pending(request=request, future=future)
        with self._state_lock:
            if self._closed:
                raise BatchWorkerClosed("batch worker is closed")
            # Once admitted, model work must finish even if its HTTP waiter disconnects.
            future.set_running_or_notify_cancel()
            try:
                self._queue.put_nowait(pending)
            except queue.Full as exc:
                raise BatchQueueFull("backend request queue is full") from exc
            self._total_requests += 1
        return future

    def stats(self) -> BatchStats:
        with self._state_lock:
            return BatchStats(
                total_requests=self._total_requests,
                total_batches=self._total_batches,
                max_observed_batch_size=self._max_observed_batch_size,
            )

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
        self._queue.put(self._stop)
        self._thread.join()

    def _run(self) -> None:
        while True:
            first = self._queue.get()
            if first is self._stop:
                self._queue.task_done()
                return
            batch = [first]
            deadline = time.monotonic() + self._max_wait_s
            should_stop = False
            while len(batch) < self._max_batch_size:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    item = self._queue.get(timeout=remaining)
                except queue.Empty:
                    break
                if item is self._stop:
                    self._queue.task_done()
                    should_stop = True
                    break
                batch.append(item)

            pending_batch = [item for item in batch if isinstance(item, _Pending)]
            self._execute(pending_batch)
            for _ in pending_batch:
                self._queue.task_done()
            if should_stop:
                return

    def _execute(
        self,
        batch: Sequence[_Pending[RequestT, ResponseT]],
    ) -> None:
        with self._state_lock:
            self._total_batches += 1
            self._max_observed_batch_size = max(
                self._max_observed_batch_size,
                len(batch),
            )
        try:
            results = list(self._process_batch([pending.request for pending in batch]))
            if len(results) != len(batch):
                raise RuntimeError(
                    "batch processor returned "
                    f"{len(results)} results for {len(batch)} requests"
                )
        except BaseException as exc:  # noqa: BLE001 - propagate worker failures.
            results = [exc] * len(batch)

        for pending, result in zip(batch, results, strict=True):
            if isinstance(result, BaseException):
                pending.future.set_exception(result)
            else:
                pending.future.set_result(result)
