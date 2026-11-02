"""Bounded scheduling and lifecycle for segmentation backends."""
from __future__ import annotations

import threading
from concurrent.futures import Future

from uni_robot_agent.services.batching import BatchQueueFull, BatchWorker, BatchWorkerClosed
from uni_robot_agent.services.errors import ServiceError

from .backend import PerceptionBackend, SegmentInput, SegmentationResult
from .contracts import PerceptionInfo


class PerceptionService:
    def __init__(
        self, backend: PerceptionBackend, *, max_batch_size: int = 2,
        max_wait_ms: float = 5, max_queue_size: int = 64,
    ) -> None:
        self.backend = backend
        self._close_lock = threading.Lock()
        self._closed = False
        self._config = {
            "max_batch_size": max_batch_size, "max_wait_ms": max_wait_ms,
            "max_queue_size": max_queue_size,
        }
        self._batcher = BatchWorker[SegmentInput, SegmentationResult](
            backend.process_batch, max_batch_size=max_batch_size,
            max_wait_ms=max_wait_ms, max_queue_size=max_queue_size,
            name="perception-batch-worker",
        )

    def info(self) -> PerceptionInfo:
        stats = self._batcher.stats()
        return PerceptionInfo(
            model=self.backend.name, capabilities=list(self.backend.capabilities),
            batching={
                **self._config, **self.backend.batching_capabilities,
                "total_requests": stats.total_requests,
                "total_batches": stats.total_batches,
                "max_observed_batch_size": stats.max_observed_batch_size,
            },
        )

    def enqueue(self, request: SegmentInput) -> Future[SegmentationResult]:
        try:
            return self._batcher.enqueue(request)
        except BatchQueueFull as exc:
            raise ServiceError("queue_full", str(exc), status=503) from exc
        except BatchWorkerClosed as exc:
            raise ServiceError("service_closed", str(exc), status=503) from exc

    def segment(self, request: SegmentInput) -> SegmentationResult:
        return self.enqueue(request).result()

    def close(self) -> None:
        # Drain accepted work before releasing the model and its embedding cache.
        with self._close_lock:
            if self._closed:
                return
            self._batcher.close()
            self.backend.close()
            self._closed = True
