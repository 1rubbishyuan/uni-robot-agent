"""Stable telemetry data models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class TraceContext:
    """Minimal call identity propagated across threads and RPC boundaries."""

    trace_id: str
    span_id: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class SpanRecord:
    """One operation with start and end boundaries."""

    name: str
    trace_id: str
    span_id: str
    parent_span_id: str | None
    start_wall_ns: int
    duration_ns: int
    status: str
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": "span", **asdict(self)}


@dataclass(frozen=True)
class MetricRecord:
    """Aggregated low-frequency metrics without per-tick disk writes."""

    name: str
    wall_ns: int
    values: dict[str, int | float]
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": "metric", **asdict(self)}
