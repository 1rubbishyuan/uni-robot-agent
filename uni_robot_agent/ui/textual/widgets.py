"""Display components for the TUI ChatLog."""

from __future__ import annotations

import json
from typing import Any

from textual.containers import ScrollableContainer
from textual.widgets import Static


class ChatLog(ScrollableContainer):
    """Main chat region that follows the latest content."""

    DEFAULT_CSS = """
    ChatLog {
        height: 1fr;
        overflow-y: auto;
        padding: 0 1;
    }
    """

    def watch_children(self) -> None:
        self.scroll_end(animate=False)


class SessionHeader(Static):
    """Session information header."""

    DEFAULT_CSS = """
    SessionHeader {
        background: $primary-background;
        color: $text;
        padding: 0 1;
        height: 1;
    }
    """

    def set_info(self, integration: str, agent_backend: str, model: str | None) -> None:
        self.update(
            f"integration=[bold]{integration}[/]  agent=[bold]{agent_backend}[/]"
            f"  model=[bold]{model}[/]"
        )


class TurnBlock(Static):
    """Assistant text display."""

    DEFAULT_CSS = """
    TurnBlock {
        margin: 0 0 0 2;
        color: $text;
    }
    """

    def __init__(self, text: str, turn: int = 0) -> None:
        super().__init__(f"[magenta]●[/] {text}")


class ToolCallBlock(Static):
    """Tool-call display."""

    DEFAULT_CSS = """
    ToolCallBlock {
        margin: 0 0 0 2;
        color: $text-muted;
    }
    """

    def __init__(self, name: str, arguments: dict[str, Any]) -> None:
        args_str = _format_args(arguments)
        super().__init__(f"[cyan]⚙[/] [bold]{name}[/]({args_str})")


class ToolCallBatchBlock(Static):
    """Header for tools emitted by one model response."""

    DEFAULT_CSS = """
    ToolCallBatchBlock {
        margin: 1 0 0 2;
        color: $text-muted;
    }
    """

    def __init__(self, batch_id: str, tool_names: tuple[str, ...]) -> None:
        names = ", ".join(tool_names)
        super().__init__(
            f"[blue]◆[/] [bold]{batch_id}[/] · tool batch ×{len(tool_names)}: {names}"
        )


class ToolResultBlock(Static):
    """Tool-result display."""

    DEFAULT_CSS = """
    ToolResultBlock {
        margin: 0 0 0 2;
        color: $text-muted;
    }
    """

    def __init__(self, name: str, success: bool, output: Any) -> None:
        icon = "[green]✓[/]" if success else "[red]✗[/]"
        summary = _format_output(output)
        super().__init__(f"{icon} {name}: {summary}")


class ErrorBlock(Static):
    """Error display."""

    DEFAULT_CSS = """
    ErrorBlock {
        margin: 0 0 0 2;
        color: red;
    }
    """

    def __init__(self, message: str) -> None:
        super().__init__(f"[red bold]⚠[/] {message[:300]}")


class UserBlock(Static):
    """User input display."""

    DEFAULT_CSS = """
    UserBlock {
        margin: 1 0 0 0;
        padding: 0 1;
        background: $surface;
    }
    """

    def __init__(self, text: str) -> None:
        super().__init__(f"[bold blue]>[/] {text}")


def _format_args(args: dict[str, Any], max_len: int = 80) -> str:
    if not args:
        return ""
    parts = []
    for k, v in args.items():
        v_str = json.dumps(v, ensure_ascii=False, default=str)
        if len(v_str) > 30:
            v_str = v_str[:27] + "..."
        parts.append(f"{k}={v_str}")
    result = ", ".join(parts)
    if len(result) > max_len:
        result = result[: max_len - 3] + "..."
    return result


def _format_output(output: Any, max_len: int = 120) -> str:
    if output is None:
        return ""
    if isinstance(output, dict):
        keys = [k for k in output if k != "success"]
        parts = []
        for k in keys[:3]:
            v = json.dumps(output[k], ensure_ascii=False, default=str)
            if len(v) > 40:
                v = v[:37] + "..."
            parts.append(f"{k}={v}")
        return ", ".join(parts)[:max_len]
    return str(output)[:max_len]
