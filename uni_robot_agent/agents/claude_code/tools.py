"""Register tool execution callbacks with the Claude Agent SDK."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import time

from uni_robot_agent.events import AgentActivity
from uni_robot_agent.services.mcp.results import encode_call_error, encode_call_result

CALL_ID = "_uni_robot_agent_tool_use_id"
PREFIX = "mcp__uni_robot_agent__"


class ClaudeTools:
    """Carry hook IDs through MCP without changing the model-visible tool schemas."""

    def __init__(self, executor, binding, *, timeout_s=60):
        self.executor = executor
        self.binding = binding
        self.timeout_s = timeout_s
        self.specs = {spec["name"]: spec for spec in executor.get_tool_schemas()}
        self.binding.tool_names = {PREFIX + name for name in self.specs}
        self.pool = ThreadPoolExecutor(max_workers=16, thread_name_prefix="claude-tools")
        self.pending = set()
        self.closed = False

    async def pre_tool(self, data, tool_use_id, context):
        call_id = data.get("tool_use_id") or tool_use_id
        name = data.get("tool_name", "")
        if name.startswith("mcp__") and (not name.startswith(PREFIX) or name[len(PREFIX):] not in self.specs):
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                           "permissionDecisionReason": "Tool is not in the uni-robot-agent tool set"}}
        if not call_id:
            raise ValueError("Claude tool hook omitted tool_use_id")
        agent_id = data.get("agent_id") or self.binding.thread_id
        deadline = None if self.timeout_s is None else time.monotonic() + self.timeout_s
        try:
            while not self.binding.observe_call(call_id, agent_id):
                if deadline is not None and time.monotonic() >= deadline:
                    raise TimeoutError("Claude model response delivery was not confirmed")
                await asyncio.sleep(0.01)
            if name == PREFIX + "finish" and agent_id != self.binding.thread_id:
                raise PermissionError("Only the main agent may finish the episode; return findings to the parent")
        except (RuntimeError, ValueError, TimeoutError, PermissionError) as error:
            self.binding.tool_result(call_id, failed=True)
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                           "permissionDecisionReason": str(error)}}
        output = {"hookEventName": "PreToolUse", "permissionDecision": "allow"}
        # Native tools retain their SDK schema and executor. Only robot calls
        # carry the private identity through the MCP callback.
        if name.startswith(PREFIX):
            output["updatedInput"] = {**data["tool_input"], CALL_ID: call_id}
        return {"hookSpecificOutput": output}

    async def tool_completed(self, data, tool_use_id, context):
        self.binding.tool_result(data.get("tool_use_id") or tool_use_id, failed=False)
        return {}

    async def tool_failed(self, data, tool_use_id, context):
        call_id = data.get("tool_use_id") or tool_use_id
        self.binding.tool_result(call_id, failed=True)
        return {}

    async def subagent_event(self, data, tool_use_id, context):
        agent_id = data["agent_id"]
        if self.binding.bus:
            self.binding.bus.emit(AgentActivity(
                kind="started" if data["hook_event_name"] == "SubagentStart" else "turn_completed",
                agent_id=agent_id, parent_agent_id=self.binding.agent_parents.get(agent_id, ""),
                label=data.get("agent_type", ""),
            ))
        return {}

    async def call(self, name, arguments):
        arguments = dict(arguments or {})
        call_id = arguments.pop(CALL_ID, None)
        try:
            if self.closed or name not in self.specs or not call_id:
                raise ValueError("Tool callback is missing its uni-robot-agent identity")
            deadline = None if self.timeout_s is None else time.monotonic() + self.timeout_s
            while self.binding.claim(call_id, PREFIX + name, arguments) is None:
                if deadline is not None and time.monotonic() >= deadline:
                    raise TimeoutError("Claude tool dispatch did not reach its declared batch order")
                await asyncio.sleep(0.01)

            def execute():
                import jsonschema
                try:
                    jsonschema.validate(arguments, self.specs[name]["input_schema"])
                    from uni_robot_agent.execution.correlation import call_context
                    with call_context(call_id, self.binding.calls[call_id].exchange_id):
                        return encode_call_result(self.executor.call(name, arguments))
                except Exception as error:
                    return encode_call_error(error)
                finally:
                    # A cancelled SDK waiter is not evidence that a physical action stopped.
                    self.binding.finish(call_id)

            future = self.pool.submit(execute)
            self.pending = {item for item in self.pending if not item.done()}
            self.pending.add(future)
            return await asyncio.shield(asyncio.wrap_future(future))
        except Exception as error:
            return encode_call_error(error)

    def server(self):
        from mcp.types import CallToolResult, Tool
        from .sdk_compat import build_tool_server

        tools = [Tool(name=spec["name"], description=spec.get("description", ""),
                      inputSchema=spec["input_schema"]) for spec in self.specs.values()]

        async def call_tool(name, arguments):
            return CallToolResult.model_validate(await self.call(name, arguments))

        server = build_tool_server(tools, call_tool)
        return {"type": "sdk", "name": "uni_robot_agent", "instance": server}

    async def close(self):
        self.closed = True
        self.binding.cancel()
        pending = [asyncio.wrap_future(future) for future in self.pending if not future.done()]
        try:
            if pending:
                _, remaining = await asyncio.wait(pending, timeout=15)
                if remaining:
                    raise RuntimeError("Claude tool execution is still active during cleanup")
        finally:
            self.pool.shutdown(wait=False, cancel_futures=True)
