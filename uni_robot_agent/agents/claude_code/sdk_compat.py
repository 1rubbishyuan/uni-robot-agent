"""Pinned SDK boundary for an MCP server with hook-only call identities.

The public SDK helper validates arguments before invoking the tool callback.
Hook metadata must be removed first, which requires the pinned low-level builder.
"""
from importlib.metadata import version


def context_env():
    """Disable the pinned CLI's synthetic token-budget reminders."""
    return {"CLAUDE_CODE_TOTAL_TOKENS_REMINDER": "off"}


def mcp_description_env(specs):
    """Keep complete MCP descriptions in the pinned CLI's model requests."""
    # The CLI defaults to 2048 JavaScript UTF-16 code units, not Python characters.
    length = max((len(spec.get("description", "").encode("utf-16-le")) // 2
                  for spec in specs), default=0)
    return {"CLAUDE_CODE_MAX_MCP_DESCRIPTION_LENGTH": str(max(2048, length))}


def build_tool_server(tools, callback):
    installed = version("claude-agent-sdk")
    if installed != "0.2.165":
        raise RuntimeError(f"Claude MCP identity bridge requires claude-agent-sdk==0.2.165, got {installed}")
    from claude_agent_sdk._internal._mcp_compat import build_tool_server as build
    return build("uni_robot_agent", "1.0.0", tools, callback)
