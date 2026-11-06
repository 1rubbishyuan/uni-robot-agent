"""Claude Code SDK adapter; model transport and robot execution stay in uni-robot-agent."""
from __future__ import annotations
import asyncio
from contextlib import aclosing
import os
from pathlib import Path
import time
from urllib.parse import urlsplit

from uni_robot_agent.events import AgentRunStarted
from uni_robot_agent.agents.contracts import AgentCapabilities, AgentResult, CodePolicyContext
from uni_robot_agent.agents.runtime import ModelRuntime
from uni_robot_agent.provider.protocols.messages import MessagesProtocol
from uni_robot_agent.provider.registry import build_provider_adapter
from .artifacts import finalize_model_records
from .config import ClaudeConfig
from .events import ClaudeEvents
from .model_binding import ClaudeModelBinding
from .sdk_compat import mcp_description_env, context_env
from .tools import ClaudeTools


# Keep native computation and file access without unrelated coding-agent workflows.
NATIVE_TOOLS = ("Bash", "Read", "Write", "Edit", "Glob", "Grep")


class ClaudeCodeAgent(ClaudeConfig):
    capabilities = AgentCapabilities()

    def __init__(self, *, model="sonnet", timeout_s=None, cwd=None, api_key=None,
                 max_budget_usd=None, effort=None):
        self._model, self._cwd, self._api_key = model, cwd, api_key
        self._budget, self._effort = max_budget_usd, effort
        self._deadline = None if timeout_s is None else time.monotonic() + timeout_s
        self._loop = self._task = None
        self._cancel_reason = None
        self._cleanup_error = None
        self._binding = ClaudeModelBinding()
        self._code_policy = None

    def model_runtime(self, profile, event_bus=None):
        adapter = build_provider_adapter(profile, client_protocol=self.model_protocol)
        self._binding = ClaudeModelBinding(event_bus)
        return ModelRuntime("claude_messages", adapter, MessagesProtocol(keepalive_interval_s=60),
                            self._binding, finalize_model_records)

    def finalize_without_provider(self, result, session_dir):
        if session_dir is not None:
            result.train_error = "Claude native model records require --provider-url"

    def cancel(self, reason):
        if self._code_policy is not None:
            self._code_policy.revoke()
        self._cancel_reason = reason
        self._binding.cancel()
        if self._loop is not None and self._task is not None and not self._task.done():
            self._loop.call_soon_threadsafe(self._task.cancel)

    def close(self):
        if self._cleanup_error:
            raise RuntimeError(self._cleanup_error)
        if self._task is not None and not self._task.done():
            self.cancel("cancelled")
            raise RuntimeError("Claude SDK run has not finished cleanup")

    def run(self, *, prompt, task, toolkit, executor, max_turns=0,
            event_bus=None, session_dir=None, model_endpoint=None, code_policy: CodePolicyContext | None = None):
        self._code_policy = code_policy
        try:
            return asyncio.run(self._run_async(prompt, task, executor, max_turns,
                                               event_bus, session_dir, model_endpoint, code_policy))
        finally:
            self._loop = self._task = None

    async def _run_async(self, prompt, task, executor, max_turns, bus, session_dir, model_endpoint, code_policy=None):
        import claude_agent_sdk as sdk
        self._loop, self._task = asyncio.get_running_loop(), asyncio.current_task()
        self._cancel_reason = None
        tools = ClaudeTools(executor, self._binding, timeout_s=(
            None if self._deadline is None else max(0, self._deadline - time.monotonic())
        ))
        state = ClaudeEvents(bus, self._binding)
        started = time.monotonic()
        cleanup_error = None
        remaining = None if self._deadline is None else max(0, self._deadline - started)
        deadline = asyncio.timeout(remaining)
        try:
            env = {
                "CLAUDE_CODE_USE_BEDROCK": "0", "CLAUDE_CODE_USE_VERTEX": "0",
                "CLAUDE_CODE_USE_FOUNDRY": "0", "CLAUDE_CODE_OAUTH_TOKEN": "",
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                # uni-robot-agent retries a complete buffered exchange, before tool delivery.
                "CLAUDE_CODE_MAX_RETRIES": "0",
                # Keep native helpers and subagents on the explicitly selected provider model.
                "ANTHROPIC_DEFAULT_HAIKU_MODEL": self._model,
                "ANTHROPIC_DEFAULT_SONNET_MODEL": self._model,
                "ANTHROPIC_DEFAULT_OPUS_MODEL": self._model,
                "CLAUDE_CODE_SUBAGENT_MODEL": self._model,
                **mcp_description_env(tools.specs.values()),
                **context_env(),
                **(code_policy.env if code_policy else {}),
            }
            if model_endpoint:
                env["ANTHROPIC_BASE_URL"] = model_endpoint.rstrip("/").removesuffix("/v1")
                host = urlsplit(model_endpoint).hostname
                bypass = ",".join(filter(None, [os.environ.get("NO_PROXY") or os.environ.get("no_proxy"), host]))
                env.update(NO_PROXY=bypass, no_proxy=bypass)
            token = self._api_key or os.environ.get("ANTHROPIC_AUTH_TOKEN")
            if token:
                env.update(ANTHROPIC_AUTH_TOKEN=token, ANTHROPIC_API_KEY="")
            if session_dir is not None:
                config_dir = Path(session_dir) / ".claude"
                config_dir.mkdir(parents=True, exist_ok=True)
                env["CLAUDE_CONFIG_DIR"] = str(config_dir.resolve())
            options = sdk.ClaudeAgentOptions(
                model=self._model, system_prompt=prompt.system,
                max_turns=max_turns or None, max_budget_usd=self._budget,
                max_buffer_size=64 * 1024 * 1024,
                forward_subagent_text=True,
                tools=list(NATIVE_TOOLS),
                allowed_tools=["mcp__uni_robot_agent__" + name for name in tools.specs],
                mcp_servers={"uni_robot_agent": tools.server()}, strict_mcp_config=True,
                # The hook authorizes native tools, including access outside cwd.
                # Root sessions use hook decisions rather than bypassPermissions.
                permission_mode="dontAsk", sandbox={"enabled": False},
                setting_sources=[], skills=[], cwd=code_policy.cwd if code_policy else self._cwd,
                env=env, effort=self._effort,
                hooks={
                    "PreToolUse": [sdk.HookMatcher(hooks=[tools.pre_tool])],
                    "PostToolUse": [sdk.HookMatcher(hooks=[tools.tool_completed])],
                    "PostToolUseFailure": [sdk.HookMatcher(hooks=[tools.tool_failed])],
                    "SubagentStart": [sdk.HookMatcher(hooks=[tools.subagent_event])],
                    "SubagentStop": [sdk.HookMatcher(hooks=[tools.subagent_event])],
                },
            )
            if bus:
                bus.emit(AgentRunStarted(instruction=task, model=self._model,
                                        agent_backend="claude_code", tool_event_source="execution"))
            async with deadline:
                async with aclosing(sdk.query(prompt=prompt.user, options=options)) as messages:
                    async for message in messages:
                        state.consume(message)
        except asyncio.CancelledError:
            state.stop_reason = self._cancel_reason or "cancelled"
        except TimeoutError as error:
            if deadline.expired():
                state.stop_reason = "wall_time_limit"
            else:
                state.error = f"TimeoutError: {error}"
        except Exception as error:
            if not (state.stop_reason == "max_turns" and type(error).__name__ == "ResultError"):
                state.error = f"{type(error).__name__}: {error}"
        finally:
            try:
                await tools.close()
            except Exception as error:
                cleanup_error = self._cleanup_error = str(error)
                state.error = state.error or cleanup_error
        finish = code_policy.finish_request() if code_policy else state.finish_request
        message = state.error or ((finish.get("summary") or finish.get("message")) if finish else None)
        message = message or "\n".join(state.texts) or "Claude session ended without output"
        return AgentResult(
            success=finish is not None and state.completed and not state.error and not state.stop_reason,
            message=message, error=state.error, cleanup_error=cleanup_error,
            stop_reason=state.stop_reason, turns_used=state.turns, tool_calls=state.tool_calls,
            steps_used=state.steps, elapsed_s=time.monotonic() - started,
            main_thread_id=self._binding.thread_id, trace_format="claude_messages",
        )
