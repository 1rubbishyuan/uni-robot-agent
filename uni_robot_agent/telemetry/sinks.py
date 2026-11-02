"""Telemetry sinks independent of the control hot path."""

from __future__ import annotations

import json
import queue
import threading
from collections import defaultdict
from pathlib import Path
from typing import Protocol

from uni_robot_agent.telemetry.model import MetricRecord, SpanRecord

Record = SpanRecord | MetricRecord


class TelemetrySink(Protocol):
    def emit(self, record: Record) -> None: ...

    def close(self) -> None: ...


class MemorySink:
    """In-memory sink for tests and interactive inspection."""

    def __init__(self) -> None:
        self.records: list[Record] = []

    def emit(self, record: Record) -> None:
        self.records.append(record)

    def close(self) -> None:
        pass


class JsonlSink:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file = path.open("a", encoding="utf-8")

    def emit(self, record: Record) -> None:
        self._file.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")

    def close(self) -> None:
        self._file.close()


class SummarySink:
    """Generate comparable timing summaries when the session closes."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._durations: dict[str, list[int]] = defaultdict(list)
        self._metrics: dict[str, list[dict[str, int | float]]] = defaultdict(list)

    def emit(self, record: Record) -> None:
        if isinstance(record, SpanRecord):
            self._durations[record.name].append(record.duration_ns)
        else:
            self._metrics[record.name].append(dict(record.values))

    def close(self) -> None:
        spans = {}
        for name, values in sorted(self._durations.items()):
            ordered = sorted(values)
            spans[name] = {
                "count": len(values),
                "total_ms": round(sum(values) / 1e6, 3),
                "mean_ms": round(sum(values) / len(values) / 1e6, 3),
                "p50_ms": round(ordered[(len(ordered) - 1) // 2] / 1e6, 3),
                "max_ms": round(ordered[-1] / 1e6, 3),
            }
        metrics = {}
        for name, samples in sorted(self._metrics.items()):
            fields = {}
            for key in sorted({key for sample in samples for key in sample}):
                values = [sample[key] for sample in samples if key in sample]
                fields[key] = {
                    "sum": sum(values),
                    "mean": sum(values) / len(values),
                    "min": min(values),
                    "max": max(values),
                    "last": values[-1],
                }
            metrics[name] = {"samples": len(samples), "fields": fields}
        self._path.write_text(
            json.dumps({"spans": spans, "metrics": metrics}, indent=2),
            encoding="utf-8",
        )


class CompositeSink:
    def __init__(self, *sinks: TelemetrySink) -> None:
        self._sinks = sinks

    def emit(self, record: Record) -> None:
        for sink in self._sinks:
            sink.emit(record)

    def close(self) -> None:
        for sink in self._sinks:
            sink.close()


class AsyncSink:
    """Bounded asynchronous sink; drop metrics on overflow without blocking environment control."""

    _STOP = object()

    def __init__(self, sink: TelemetrySink, *, capacity: int = 4096) -> None:
        self._sink = sink
        self._queue: queue.Queue[Record | object] = queue.Queue(capacity)
        self._dropped = 0
        self._closed = False
        self._thread = threading.Thread(
            target=self._drain,
            name="uni_robot_agent-telemetry",
            daemon=True,
        )
        self._thread.start()

    @property
    def dropped(self) -> int:
        return self._dropped

    def emit(self, record: Record) -> None:
        try:
            self._queue.put_nowait(record)
        except queue.Full:
            self._dropped += 1

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Control has already stopped; wait for accepted records to finish writing.
        self._queue.put(self._STOP)
        self._thread.join()
        self._sink.close()

    def _drain(self) -> None:
        while True:
            record = self._queue.get()
            if record is self._STOP:
                return
            self._sink.emit(record)
