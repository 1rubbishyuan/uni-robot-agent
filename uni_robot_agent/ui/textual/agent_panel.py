"""On-demand Textual panel for subagent activity."""

from __future__ import annotations

from textual.widgets import Static

from uni_robot_agent.events import AgentActivity
from uni_robot_agent.ui.state import AgentView

_STATUS_STYLE = {
    "starting": "blue",
    "running": "yellow",
    "idle": "cyan",
    "completed": "green",
    "closed": "dim green",
    "failed": "red",
}


class AgentPanel(Static):
    DEFAULT_CSS = """
    AgentPanel {
        display: none;
        width: 44;
        height: 1fr;
        border: round blue;
        padding: 0 1;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._view = AgentView()
        self._expanded = False

    def apply_event(self, event: AgentActivity) -> None:
        self._view.apply(event)
        if self._view.children():
            self.styles.display = "block"
        self._render()

    def toggle_expanded(self) -> None:
        self._expanded = not self._expanded
        self._render()

    def _render(self) -> None:
        states = self._view.children()
        if not states:
            return
        lines = [f"[bold blue]Subagents {len(states)}[/]"]
        for state in states:
            style = _STATUS_STYLE.get(state.status, "white")
            tools = ", ".join(state.current_tools) or "—"
            lines.append(
                f"[bold]{state.display_name}[/] "
                f"[{style}]{state.status}[/] "
                f"[dim]{state.elapsed_s():.1f}s[/]"
            )
            lines.append(f"  [dim]tool[/] {tools[:55]}")
            if self._expanded and state.last_message:
                lines.append(f"  {state.last_message}")
        self.update("\n".join(lines))
