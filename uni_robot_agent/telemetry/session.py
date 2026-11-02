"""Telemetry lifecycle for one harness run."""

from __future__ import annotations

import json
import platform
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from uni_robot_agent.telemetry.sinks import AsyncSink, CompositeSink, JsonlSink, SummarySink
from uni_robot_agent.telemetry.tracer import NullTracer, Tracer

TelemetryMode = Literal["off", "standard", "debug"]


@dataclass
class TelemetrySession:
    mode: TelemetryMode
    tracer: NullTracer | Tracer
    _sink: AsyncSink | None = None
    _manifest_path: Path | None = None

    def annotate(self, **values) -> None:
        if self._manifest_path is None:
            return
        manifest = json.loads(self._manifest_path.read_text(encoding="utf-8"))
        manifest.update(values)
        self._manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    def close(self) -> None:
        if self._sink is not None:
            self._sink.close()


def create_telemetry(
    session_dir: str | Path,
    mode: TelemetryMode = "off",
    *,
    component: str = "harness",
) -> TelemetrySession:
    """Create telemetry; off mode allocates no runtime resources."""
    if mode == "off":
        return TelemetrySession(mode=mode, tracer=NullTracer())

    output_dir = Path(session_dir) / "telemetry"
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "run_manifest.json"
    manifest_path.write_text(json.dumps({
        "component": component,
        "mode": mode,
        "started_wall_ns": time.time_ns(),
        "python": platform.python_version(),
    }, indent=2), encoding="utf-8")

    sink = AsyncSink(CompositeSink(
        JsonlSink(output_dir / "trace.jsonl"),
        SummarySink(output_dir / "timing_summary.json"),
    ))
    return TelemetrySession(
        mode=mode,
        tracer=Tracer(sink),
        _sink=sink,
        _manifest_path=manifest_path,
    )
