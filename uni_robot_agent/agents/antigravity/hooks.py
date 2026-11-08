"""Bind native pre-tool identities without executing tools in SDK hooks."""
import json

from uni_robot_agent.provider.protocols.gemini import tool_calls


def delivered_calls(body):
    """Remove only SDK UI labels that its native runtime removes before hooks."""
    calls = tool_calls(body)
    for call in calls:
        if call["name"].startswith("mcp_ura_"):
            for field in ("toolSummary", "toolAction"):
                value = call["arguments"].pop(field, None)
                if value is not None and not isinstance(value, str):
                    raise ValueError(f"Antigravity {field} must be a string")
    return calls


def build_hooks(binding, directory):
    from google.antigravity import types
    from google.antigravity.hooks import hooks

    @hooks.pre_tool_call_decide
    async def before_tool(data: types.ToolCall) -> types.HookResult:
        with (directory / 'tool-calls.jsonl').open('a') as archive:
            archive.write(json.dumps(data.model_dump(mode='json')) + '\n')
        if binding.cancelled:
            return types.HookResult(allow=False, message='Agent session is cancelled')
        if data.server_name != 'ura':
            return types.HookResult(allow=True)
        if (not data.id or data.name not in binding.specs
                or '_uni_robot_agent_call' in data.args):
            return types.HookResult(allow=False, message='Robot tool requires an unmodified native call ID')
        # Eager MCP loading passes local arguments here. The server consumes this
        # private field before validation; it is never added to model schemas.
        return types.HookResult(allow=True,
            modified_args={**data.args, '_uni_robot_agent_call': data.id})

    return [before_tool]
