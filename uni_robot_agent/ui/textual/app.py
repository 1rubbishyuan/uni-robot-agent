"""Textual terminal frontend and UI-thread event delivery."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Footer, Header, Input, Static

from uni_robot_agent.context.prompt import TaskPromptBuilder
from uni_robot_agent.events import (
    AgentActivity,
    AssistantText,
    ErrorEvent,
    EventBus,
    AgentRunFinished,
    ToolCall,
    ToolCallBatch,
    ToolResult,
)
from uni_robot_agent.events.subscribers import EventSubscriptions, TuiSubscriber
from uni_robot_agent.execution import ToolExecutor
from uni_robot_agent.session import SingleTaskSession
from uni_robot_agent.tools.toolkit import Toolkit
from uni_robot_agent.ui.textual.agent_panel import AgentPanel
from uni_robot_agent.ui.textual.widgets import (
    ChatLog,
    ErrorBlock,
    SessionHeader,
    ToolCallBatchBlock,
    ToolCallBlock,
    ToolResultBlock,
    TurnBlock,
    UserBlock,
)

_dbg = logging.getLogger("uni_robot_agent.ui.textual")


class UniRobotAgentApp(App):
    """Full-screen event view for a single task."""

    TITLE = "uni-robot-agent"
    CSS = """
    Screen {
        layout: vertical;
    }
    #status {
        dock: bottom;
        height: 1;
        background: $primary-background;
        padding: 0 1;
    }
    #workspace {
        height: 1fr;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("ctrl+c", "quit", "Quit", show=True),
        Binding("a", "toggle_agent_panel", "Agents", show=True),
    ]

    def __init__(
        self,
        *,
        integration_name: str,
        agent_backend: str,
        model: str | None,
        agent_options: dict[str, Any] | None,
        toolkit: Toolkit,
        executor: ToolExecutor,
        prompt_builder: TaskPromptBuilder,
        bus: EventBus,
        session_dir: Path,
        artifacts,
        tracer,
        max_turns: int = 50,
    ) -> None:
        super().__init__()
        self._integration_name = integration_name
        self._agent_backend = agent_backend
        self._model = model
        self._toolkit = toolkit
        self._executor = executor
        self._prompt_builder = prompt_builder
        self._bus = bus
        self._subscriptions = EventSubscriptions(bus)
        self._session_dir = session_dir
        self._tracer = tracer
        self._max_turns = max_turns

        self._task_session = SingleTaskSession(
            agent_backend=agent_backend,
            model=model,
            agent_options=agent_options,
            toolkit=toolkit,
            executor=executor,
            prompt_builder=prompt_builder,
            bus=bus,
            session_dir=session_dir,
            artifacts=artifacts,
            tracer=tracer,
            max_turns=max_turns,
        )
        self._busy = False
        self._submitted = False
        self._agent_thread: threading.Thread | None = None
        self._steps = 0

    def compose(self) -> ComposeResult:
        yield Header()
        session_hdr = SessionHeader()
        session_hdr.set_info(self._integration_name, self._agent_backend, self._model)
        yield session_hdr
        with Horizontal(id="workspace"):
            yield ChatLog()
            yield AgentPanel()
        yield Input(placeholder="Enter one task (or /quit, /status)")
        yield Static("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self._subscriptions.add(TuiSubscriber(self._render_event, self.call_later))
        self._update_status()

    def on_unmount(self) -> None:
        try:
            self._subscriptions.close()
        finally:
            self._task_session.cancel()

    def close(self) -> None:
        """Wait after the UI loop stops so model archives finish before storage closes."""
        try:
            self._task_session.cancel()
        finally:
            if self._agent_thread is not None:
                self._agent_thread.join()
            self._task_session.close()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        _dbg.debug("INPUT: text=%r _running=%s", text, self._busy)
        if not text:
            return
        event.input.value = ""

        if text.startswith("/"):
            self._handle_command(text)
            return

        if self._submitted:
            chat = self.query_one(ChatLog)
            chat.mount(ErrorBlock("This TUI accepts one task. Start a new TUI for another task."))
            return

        chat = self.query_one(ChatLog)
        chat.mount(UserBlock(text))
        self._submitted = True
        event.input.placeholder = "Task submitted — /status or /quit"
        self._busy = True
        self._update_status("running...")
        _dbg.debug("STARTING agent thread for: %s", text)
        self._agent_thread = threading.Thread(target=self._run_agent, args=(text,), daemon=True)
        self._agent_thread.start()

    def _run_agent(self, task: str) -> None:
        import traceback as tb

        _dbg.debug("THREAD started for task: %s", task)
        try:
            result = self._task_session.run(task)
            _dbg.debug(
                "THREAD done: success=%s turns=%d", result.success, result.turns_used
            )
            if result.error:
                _dbg.debug("Agent stopped with error: %s", result.error)
            if result.train_partial:
                _dbg.debug("Partial SFT export: %s", result.train_stop_reason)
            if result.train_error:
                if result.train_debug_path:
                    _dbg.debug(
                        "SFT export failed validation: %s; debug trajectory: %s",
                        result.train_error,
                        result.train_debug_path,
                    )
                else:
                    _dbg.debug("SFT export unavailable: %s", result.train_error)
        except Exception as e:
            err_detail = f"Agent error: {type(e).__name__}: {e}\n{tb.format_exc()}"
            _dbg.debug("THREAD error: %s", err_detail)
            self.call_later(self._show_error, err_detail)
        finally:
            self._busy = False
            self.call_later(self._update_status)

    def _show_error(self, message: str) -> None:
        chat = self.query_one(ChatLog)
        chat.mount(ErrorBlock(message[:500]))
        chat.scroll_end(animate=False)

    def _handle_command(self, cmd: str) -> None:
        chat = self.query_one(ChatLog)
        parts = cmd.split()
        name = parts[0].lower()

        if name in ("/quit", "/q"):
            self.exit()
        elif name == "/status":
            chat.mount(
                Static(
                    f"[dim]turns={self._task_session.turns}  steps={self._steps}  "
                    f"running={self._busy}[/]"
                )
            )
        else:
            chat.mount(ErrorBlock(f"Unknown command: {name}"))

    def _render_event(self, event) -> None:
        """Render EventBus events into ChatLog through call_from_thread."""
        chat = self.query_one(ChatLog)

        if isinstance(event, AgentActivity):
            self.query_one(AgentPanel).apply_event(event)
            if event.label != "main" or not event.tool_names:
                return
            name = event.tool_names[0]
            if event.kind == "tool_started":
                call = event.tool_calls[0] if event.tool_calls else {}
                chat.mount(ToolCallBlock(name, call.get("arguments", {})))
                self._steps += 1
            elif event.kind in {"tool_completed", "tool_failed"}:
                chat.mount(
                    ToolResultBlock(
                        name,
                        event.kind == "tool_completed",
                        None,
                    )
                )
            else:
                return
        elif isinstance(event, AssistantText):
            chat.mount(TurnBlock(event.text, turn=event.turn))
        elif isinstance(event, ToolCallBatch):
            chat.mount(ToolCallBatchBlock(event.batch_id, event.tool_names))
        elif isinstance(event, ToolCall):
            chat.mount(ToolCallBlock(event.tool_name, event.arguments))
            self._steps += 1
        elif isinstance(event, ToolResult):
            chat.mount(ToolResultBlock(event.tool_name, event.success, event.output))
        elif isinstance(event, ErrorEvent):
            chat.mount(ErrorBlock(event.message))
        elif isinstance(event, AgentRunFinished):
            status = "success" if event.success else "failed"
            chat.mount(Static(f"[dim]── session end ({status}) ──[/]"))
            self._update_status()

        chat.scroll_end(animate=False)

    def action_toggle_agent_panel(self) -> None:
        self.query_one(AgentPanel).toggle_expanded()

    def on_resize(self, event) -> None:
        """Move agents below the main timeline on narrow terminals."""
        workspace = self.query_one("#workspace", Horizontal)
        panel = self.query_one(AgentPanel)
        narrow = event.size.width < 110
        workspace.styles.layout = "vertical" if narrow else "horizontal"
        if narrow:
            panel.styles.width = "1fr"
            panel.styles.height = 8
        else:
            panel.styles.width = 44
            panel.styles.height = "1fr"

    def _update_status(self, extra: str = "") -> None:
        status = self.query_one("#status", Static)
        parts = [
            f"turns={self._task_session.turns}",
            f"steps={self._steps}",
        ]
        if self._busy:
            parts.append("[yellow]running[/]")
        if extra:
            parts.append(extra)
        status.update("  ".join(parts))
