"""Explicit span API and context propagation."""

from __future__ import annotations

import contextlib
import contextvars
import time
import uuid
from collections.abc import Iterator
from typing import Any

from uni_robot_agent.telemetry.model import MetricRecord, SpanRecord, TraceContext
from uni_robot_agent.telemetry.sinks import TelemetrySink

_current_context: contextvars.ContextVar[TraceContext | None] = contextvars.ContextVar(
    "uni_robot_agent_trace_context", default=None,
)


class NullTracer:
    """No-work implementation for disabled telemetry."""

    enabled = False

    def span(self, name: str, **attributes: Any):
        return contextlib.nullcontext()

    def record_duration(
        self,
        name: str,
        duration_ns: int,
        *,
        attributes: dict[str, Any] | None = None,
        context: TraceContext | None = None,
        status: str = "ok",
    ) -> None:
        pass

    def metric(
        self,
        name: str,
        values: dict[str, int | float],
        *,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        pass

    def capture(self) -> None:
        return None

    def use_context(self, context: TraceContext | None):
        return contextlib.nullcontext()


class Tracer:
    enabled = True

    def __init__(self, sink: TelemetrySink) -> None:
        self._sink = sink

    def span(self, name: str, **attributes: Any) -> Span:
        return Span(self, name, attributes)

    def record_duration(
        self,
        name: str,
        duration_ns: int,
        *,
        attributes: dict[str, Any] | None = None,
        context: TraceContext | None = None,
        status: str = "ok",
    ) -> None:
        parent = context or _current_context.get()
        trace_id = parent.trace_id if parent else uuid.uuid4().hex
        self._sink.emit(SpanRecord(
            name=name,
            trace_id=trace_id,
            span_id=uuid.uuid4().hex[:16],
            parent_span_id=parent.span_id if parent else None,
            start_wall_ns=time.time_ns() - duration_ns,
            duration_ns=duration_ns,
            status=status,
            attributes=attributes or {},
        ))

    def metric(
        self,
        name: str,
        values: dict[str, int | float],
        *,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        self._sink.emit(MetricRecord(
            name=name,
            wall_ns=time.time_ns(),
            values=values,
            attributes=attributes or {},
        ))

    def capture(self) -> TraceContext | None:
        return _current_context.get()

    @contextlib.contextmanager
    def use_context(self, context: TraceContext | None) -> Iterator[None]:
        token = _current_context.set(context)
        try:
            yield
        finally:
            _current_context.reset(token)


class Span:
    """Time an operation and restore its parent context on exit."""

    def __init__(self, tracer: Tracer, name: str, attributes: dict[str, Any]) -> None:
        self._tracer = tracer
        self._name = name
        self._attributes = attributes
        self._parent: TraceContext | None = None
        self._context: TraceContext | None = None
        self._token = None
        self._start_mono_ns = 0
        self._start_wall_ns = 0

    @property
    def context(self) -> TraceContext:
        assert self._context is not None
        return self._context

    def __enter__(self) -> Span:  # noqa: PYI034 - keep the concrete context-manager return contract.
        self._parent = _current_context.get()
        self._context = TraceContext(
            trace_id=self._parent.trace_id if self._parent else uuid.uuid4().hex,
            span_id=uuid.uuid4().hex[:16],
        )
        self._start_mono_ns = time.monotonic_ns()
        self._start_wall_ns = time.time_ns()
        self._token = _current_context.set(self._context)
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        duration_ns = time.monotonic_ns() - self._start_mono_ns
        assert self._context is not None and self._token is not None
        _current_context.reset(self._token)
        self._tracer._sink.emit(SpanRecord(
            name=self._name,
            trace_id=self._context.trace_id,
            span_id=self._context.span_id,
            parent_span_id=self._parent.span_id if self._parent else None,
            start_wall_ns=self._start_wall_ns,
            duration_ns=duration_ns,
            status="error" if exc_type else "ok",
            attributes=self._attributes,
        ))
