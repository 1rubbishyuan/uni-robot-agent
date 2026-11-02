"""Rich rendering for the agent transcript and live execution status."""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from rich.console import Console, Group, RenderableType
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.pretty import Pretty
from rich.segment import Segment, Segments
from rich.text import Text

from uni_robot_agent.events import (
    AgentActivity,
    AssistantText,
    ErrorEvent,
    Event,
    ReasoningSummary,
    AgentRunFinished,
    AgentRunStarted,
    ToolCall,
    ToolCallBatch,
    ToolQueueSnapshot,
    ToolResult,
)
from uni_robot_agent.ui.state import TaskStatus

console = Console(highlight=False)


class ConsoleRenderer:
    """Render agent traces, TODOs, and execution queues onto the terminal."""

    def __init__(self) -> None:
        self._panel = AgentTranscriptPanel(console)

    def render(self, event: Event) -> None:
        if isinstance(event, AgentRunStarted):
            self._panel.begin()
            self._main(event, _styled("session start", "bold blue"))
            self._main(
                event,
                Text.assemble(
                    ("agent=", "dim"),
                    (event.agent_backend, "cyan"),
                    ("  model=", "dim"),
                    (event.model, "cyan"),
                ),
            )
            self._main(event, Group(Text("task", style="bold"), _full_text(event.instruction)))
            return

        if isinstance(event, ToolQueueSnapshot):
            self._panel.update_queues(event.resources)
            return

        if isinstance(event, AgentActivity):
            self._render_agent_activity(event)
            return

        if isinstance(event, ReasoningSummary):
            self._main(
                event,
                Group(
                    Text("reasoning", style="bold dim"),
                    _full_text(event.text),
                ),
            )
            return

        if isinstance(event, AssistantText):
            self._main(
                event,
                Group(
                    Text(f"{_step(event.agent_step)} assistant", style="bold magenta"),
                    _full_text(event.text),
                ),
            )
            return

        if isinstance(event, ToolCallBatch):
            self._main(event, _batch_render(event.batch_id, event.tool_names))
            return

        if isinstance(event, ToolCall):
            self._main(
                event,
                _call_render(event.tool_name, event.arguments, step=event.source_step),
            )
            return

        if isinstance(event, ToolResult):
            self._main(
                event,
                _result_render(
                    event.tool_name,
                    event.success,
                    event.output,
                    step=event.tool_execution_seq,
                    images=event.images,
                ),
            )
            return

        if isinstance(event, ErrorEvent):
            self._runtime(
                event,
                Group(
                    Text(f"{_step(event.source_step)} error", style="bold red"),
                    _full_text(event.message),
                ),
            )
            return

        if isinstance(event, AgentRunFinished):
            ok = event.success if event.task_success is None else event.task_success
            style = "bold green" if ok else "bold red"
            self._main(
                event,
                _styled(
                    f"session end │ {'success' if ok else 'failed'}",
                    style,
                ),
            )
            if event.task_success is not None:
                self._main(
                    event,
                    Text(
                        f"task_success={event.task_success}  "
                        f"agent_finished={event.success}",
                    ),
                )
            self._main(
                event,
                Text(
                    f"turns={event.turns}  steps={event.steps}  "
                    f"tool_calls={event.tool_calls}  elapsed={event.elapsed_s:.1f}s",
                ),
            )
            if event.message and not ok:
                self._main(
                    event,
                    Group(
                        Text("reason", style="bold red"),
                        _full_text(event.message),
                    ),
                )
            self._panel.finish()

    def render_execution_failure(self, event: ToolResult) -> None:
        self._runtime(
            event,
            _result_render(event.tool_name, False, event.output, step=event.tool_execution_seq),
        )

    def close(self) -> None:
        self._panel.finish()

    def _render_agent_activity(self, event: AgentActivity) -> None:
        # Provider batches and SDK started events both contain original arguments; updates are idempotent.
        self._update_plan(event.tool_calls)

        if event.label == "main":
            self._render_tool_activity(event, lambda item: self._main(event, item))
            return
        if not event.parent_agent_id:
            return

        label = event.label or event.agent_id[:8]
        emit = lambda item: self._child(event, label, item)
        if event.kind == "spawned":
            emit(_styled("started", "bold blue"))
        elif event.kind == "running":
            emit(_styled("assignment running", "yellow"))
        elif event.kind == "idle":
            emit(_styled("waiting for next assignment", "cyan"))
        elif event.kind == "closed":
            emit(_styled("closed", "dim green"))
        elif event.kind == "failed":
            emit(_styled("agent failed", "bold red"))
        elif event.kind == "message" and event.text:
            emit(
                Group(
                    Text("assistant", style="bold magenta"),
                    _full_text(event.text),
                )
            )
        else:
            self._render_tool_activity(event, emit)

    @staticmethod
    def _render_tool_activity(
        event: AgentActivity,
        emit: Callable[[RenderableType], None],
    ) -> None:
        if event.kind == "tool_batch":
            emit(
                _batch_render(
                    event.exchange_id,
                    event.tool_names,
                    event.tool_calls,
                )
            )
            return
        if not event.tool_names:
            return

        name = event.tool_names[0]
        call = event.tool_calls[0] if event.tool_calls else {}
        if event.kind == "tool_started":
            emit(
                _call_render(
                    name,
                    call.get("arguments", {}),
                    call_id=str(call.get("call_id") or event.item_id),
                )
            )
        elif event.kind in {"tool_completed", "tool_failed"}:
            ok = event.kind == "tool_completed"
            emit(
                Text.assemble(
                    ("result │ ", "bold"),
                    (name, "white"),
                    ("  "),
                    ("returned" if ok else "failed", "green" if ok else "red"),
                    (f"  call_id={event.item_id}" if event.item_id else "", "dim"),
                )
            )

    def _update_plan(self, calls: tuple[dict[str, Any], ...]) -> None:
        for call in calls:
            name = str(call.get("name", "")).split("__")[-1]
            arguments = call.get("arguments", {})
            if name == "update_plan" and isinstance(arguments, dict):
                plan = arguments.get("plan")
                if isinstance(plan, list):
                    self._panel.update_plan(plan)

    def _main(self, event: Event, content: RenderableType) -> None:
        self._panel.main(
            content,
            event_seq=event.event_seq,
            timestamp=event.timestamp_unix_s,
        )

    def _child(
        self,
        event: Event,
        label: str,
        content: RenderableType,
    ) -> None:
        self._panel.child(
            label,
            content,
            event_seq=event.event_seq,
            timestamp=event.timestamp_unix_s,
        )

    def _runtime(self, event: Event, content: RenderableType) -> None:
        self._panel.runtime(
            content,
            event_seq=event.event_seq,
            timestamp=event.timestamp_unix_s,
        )


def _step(step: int) -> str:
    return f"{step:>3} │"


def _styled(value: str, style: str) -> Text:
    return Text(value, style=style)


def _full_text(value: str) -> Text:
    return Text(value, overflow="fold")


def _batch_render(
    batch_id: str,
    names: tuple[str, ...],
    calls: tuple[dict[str, Any], ...] = (),
) -> Group:
    prefix = f"{batch_id} " if batch_id else ""
    items: list[RenderableType] = [
        Text.assemble(
            (prefix, "bold blue"),
            (f"tool batch ×{len(names)} │ ", "bold cyan"),
            ", ".join(names),
        ),
    ]
    for index, call in enumerate(calls, 1):
        name = str(call.get("name", ""))
        call_id = str(call.get("call_id", ""))
        title = f"{index}. {name}"
        if call_id:
            title += f"  call_id={call_id}"
        items.append(Text(title, style="bold"))
        arguments = call.get("arguments", {})
        if arguments:
            items.append(Pretty(arguments, expand_all=True, overflow="fold"))
    return Group(*items)


def _call_render(
    name: str,
    args: dict[str, Any],
    *,
    step: int = 0,
    call_id: str = "",
) -> Group:
    prefix = f"{_step(step)} " if step else ""
    suffix = f"  call_id={call_id}" if call_id else ""
    items: list[RenderableType] = [
        Text.assemble(
            (prefix, "dim"),
            ("call │ ", "bold cyan"),
            (name, "white"),
            (suffix, "dim"),
        ),
    ]
    if args:
        items.append(Pretty(args, expand_all=True, overflow="fold"))
    return Group(*items)


def _result_render(
    name: str,
    success: bool,
    output: Any,
    *,
    step: int = 0,
    images: tuple[Any, ...] = (),
) -> Group:
    status = "ok" if success else "fail"
    items: list[RenderableType] = [
        Text.assemble(
            ((f"{_step(step)} " if step else ""), "dim"),
            ("result │ ", "bold"),
            (name, "white"),
            ("  "),
            (status, "green" if success else "red"),
        ),
    ]
    if output is not None:
        items.append(Pretty(output, expand_all=True, overflow="fold"))
    if images:
        items.append(
            Pretty(
                [
                    {
                        "name": image.name,
                        "mime_type": image.mime_type,
                        "size_bytes": len(image.data),
                    }
                    for image in images
                ],
                expand_all=True,
                overflow="fold",
            )
        )
    return Group(*items)


class AgentTranscriptPanel:
    """Render four fixed TTY regions or individual text events when redirected."""

    def __init__(self, console: Console, max_entries: int = 80) -> None:
        self._console = console
        self._main: deque[RenderableType] = deque(maxlen=max_entries)
        self._children: deque[RenderableType] = deque(maxlen=max_entries)
        self._status = TaskStatus()
        self._live: Live | None = None
        # SDK notifications and runtime callbacks use different threads; serialize rendering here.
        self._lock = threading.RLock()

    def begin(self) -> None:
        with self._lock:
            self._stop()
            self._main.clear()
            self._children.clear()
            self._status.clear()
            if self._console.is_terminal:
                self._live = Live(
                    self._render(),
                    console=self._console,
                    auto_refresh=False,
                    transient=False,
                    vertical_overflow="crop",
                )
                self._live.start()

    def main(
        self,
        content: RenderableType,
        *,
        event_seq: int = 0,
        timestamp: float | None = None,
    ) -> None:
        self._append(
            self._main,
            "MAIN",
            content,
            event_seq=event_seq,
            timestamp=timestamp,
        )

    def child(
        self,
        label: str,
        content: RenderableType,
        *,
        event_seq: int = 0,
        timestamp: float | None = None,
    ) -> None:
        self._append(
            self._children,
            label or "subagent",
            content,
            event_seq=event_seq,
            timestamp=timestamp,
        )

    def runtime(
        self,
        content: RenderableType,
        *,
        event_seq: int = 0,
        timestamp: float | None = None,
    ) -> None:
        tagged = Group(Text("RUNTIME", style="bold yellow"), content)
        self._append(
            self._main,
            "MAIN",
            tagged,
            event_seq=event_seq,
            timestamp=timestamp,
        )

    def update_plan(
        self, plan: list[dict[str, Any]] | tuple[dict[str, Any], ...]
    ) -> None:
        with self._lock:
            self._status.update_plan(plan)
            self._refresh()

    def update_queues(self, resources: dict[str, Any]) -> None:
        with self._lock:
            self._status.update_queues(resources)
            self._refresh()

    def finish(self) -> None:
        with self._lock:
            self._refresh()
            self._stop()

    def _append(
        self,
        lane: deque[RenderableType],
        label: str,
        content: RenderableType,
        *,
        event_seq: int,
        timestamp: float | None,
    ) -> None:
        with self._lock:
            title = self._title(label, event_seq, timestamp)
            entry = Group(Text(title, style="bold dim"), content)
            lane.append(entry)
            if self._live is None:
                self._console.print(entry, overflow="fold")
                return
            self._refresh()

    def _refresh(self) -> None:
        if self._live is not None:
            self._live.update(self._render(), refresh=True)

    def _render(self) -> Layout:
        height = max(8, self._console.size.height)
        status_height = max(6, min(10, height // 3))
        agent_height = max(4, height - status_height)
        content_width = max(12, self._console.size.width // 2 - 4)

        layout = Layout()
        layout.split_column(
            Layout(name="agents", size=agent_height),
            Layout(name="status", size=status_height),
        )
        layout["agents"].split_row(
            Layout(name="main"),
            Layout(name="subagent"),
        )
        layout["status"].split_row(
            Layout(name="plan"),
            Layout(name="queues"),
        )
        main = (
            Group(*self._main)
            if self._main
            else Text("Waiting for main agent…", style="dim")
        )
        children = (
            Group(*self._children)
            if self._children
            else Text("Waiting for subagent activity…", style="dim")
        )
        layout["main"].update(
            Panel(
                self._tail(main, content_width, agent_height - 2),
                title="Main agent",
                border_style="magenta",
            )
        )
        layout["subagent"].update(
            Panel(
                self._tail(children, content_width, agent_height - 2),
                title="Subagent",
                border_style="blue",
            )
        )
        layout["plan"].update(
            Panel(
                self._tail(
                    self._render_plan(),
                    content_width,
                    status_height - 2,
                ),
                title="TODO",
                border_style="green",
            )
        )
        layout["queues"].update(
            Panel(
                self._tail(
                    self._render_queues(),
                    content_width,
                    status_height - 2,
                ),
                title="Tool queue",
                border_style="yellow",
            )
        )
        return layout

    def _tail(
        self,
        renderable: RenderableType,
        width: int,
        height: int,
    ) -> Segments:
        """Keep the newest rendered lines within the panel's actual height."""
        options = self._console.options.update(
            width=width,
            height=None,
            overflow="fold",
            no_wrap=False,
        )
        lines = self._console.render_lines(
            renderable,
            options,
            pad=False,
            new_lines=False,
        )
        segments = (
            segment
            for line in lines[-max(1, height) :]
            for segment in (*line, Segment.line())
        )
        return Segments(segments)

    def _render_plan(self) -> Group:
        if not self._status.plan:
            return Group(Text("No plan published", style="dim"))
        icons = {
            "completed": ("✓", "green"),
            "in_progress": ("●", "yellow"),
            "pending": ("○", "dim"),
        }
        lines = []
        for item in self._status.plan:
            icon, style = icons.get(str(item.get("status", "")), ("?", "red"))
            lines.append(
                Text.assemble(
                    (f"{icon} ", style),
                    str(item.get("step", "")),
                )
            )
        return Group(*lines)

    def _render_queues(self) -> Group:
        if not self._status.queues:
            return Group(Text("No active background jobs", style="dim green"))
        lines: list[Text] = []
        for resource, state in sorted(self._status.queues.items()):
            lines.append(Text(str(resource), style="bold"))
            for job in state.get("running", ()):
                lines.append(
                    Text.assemble(
                        ("  ▶ ", "green"),
                        str(job.get("tool_name", "")),
                        ("  running", "dim green"),
                    )
                )
            for job in state.get("queued", ()):
                position = job.get("queue_position")
                lines.append(
                    Text.assemble(
                        (f"  {position or '?'} ", "yellow"),
                        str(job.get("tool_name", "")),
                        ("  queued", "dim yellow"),
                    )
                )
        return Group(*lines)

    @staticmethod
    def _title(label: str, event_seq: int, timestamp: float | None) -> str:
        clock = time.strftime("%H:%M:%S", time.localtime(timestamp or time.time()))
        sequence = f"#{event_seq} " if event_seq else ""
        return f"{sequence}{clock} {label}"

    def _stop(self) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None
