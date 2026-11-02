"""Environment-independent workspace file tools."""

from __future__ import annotations

import os

from uni_robot_agent.tools.toolkit import (
    ToolDef, ToolOutput, ToolProvider, ToolSpec, bind_tools, schema,
)


def tool_specs() -> list[ToolSpec]:
    return [
        ToolSpec("read_text_file",
            "Read a UTF-8 text file. Large files are truncated.",
            schema({
                "path": {"type": "string", "description": "Absolute or repo-relative path"},
                "max_chars": {"type": "integer", "description": "Max chars (default 40000)"},
            }, required=("path",)), readonly=True),
        ToolSpec("write_text_file",
            "Write a UTF-8 text file and create missing parent directories.",
            schema({
                "path": {"type": "string", "description": "File path"},
                "content": {"type": "string", "description": "Content to write"},
            }, required=("path", "content"))),
        ToolSpec("list_dir",
            "List files in a directory (non-recursive).",
            schema({"path": {"type": "string", "default": ".", "description": "Directory path"}}),
            readonly=True),
    ]


class WorkspaceProvider(ToolProvider):
    provider_id = "workspace"

    def get_tools(self) -> list[ToolDef]:
        return bind_tools(
            tool_specs(),
            provider_id=self.provider_id,
            handlers={
                "read_text_file": self.read_text_file,
                "write_text_file": self.write_text_file,
                "list_dir": self.list_dir,
            },
        )

    def read_text_file(self, path: str, max_chars: int = 40_000) -> ToolOutput:
        try:
            with open(path, "r", encoding="utf-8") as file:
                content = file.read(max_chars + 1)
            return ToolOutput(
                extra={
                    "content": content[:max_chars],
                    "truncated": len(content) > max_chars,
                }
            )
        except Exception as exc:
            return ToolOutput(success=False, error=str(exc))

    def write_text_file(self, path: str, content: str) -> ToolOutput:
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as file:
                file.write(content)
            return ToolOutput(extra={"bytes_written": len(content.encode("utf-8"))})
        except Exception as exc:
            return ToolOutput(success=False, error=str(exc))

    def list_dir(self, path: str = ".") -> ToolOutput:
        try:
            return ToolOutput(
                extra={"entries": sorted(os.listdir(path))}
            )
        except Exception as exc:
            return ToolOutput(success=False, error=str(exc))
