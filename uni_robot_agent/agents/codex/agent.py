"""Codex SDK agent.

Drive the conversation through the openai_codex Python SDK.
Expose Toolkit tools to the Codex binary through a background HTTP MCP server.
"""

from __future__ import annotations

import json
import os
import threading
import time
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from uni_robot_agent.context.prompt import Prompt
from uni_robot_agent.events import AssistantText, EventBus, ReasoningSummary, AgentRunStarted
from uni_robot_agent.agents.contracts import AgentCapabilities, AgentInterrupted, AgentResult
from uni_robot_agent.execution import ToolExecutor
from uni_robot_agent.telemetry.logging import get_logger
from uni_robot_agent.tools.toolkit import Toolkit

logger = get_logger("codex")

from .config import CodexConfig, REASONING_EFFORTS, BASE_PROMPTS, validate_agent_python
from .events import CodexAgentEventAdapter, CodexEventSource, _get, _unwrap
from .model_binding import CodexResponsesBinding
from .artifacts import finalize_model_records
from uni_robot_agent.agents.runtime import ModelRuntime
from uni_robot_agent.provider.registry import build_provider_adapter
from uni_robot_agent.provider.protocols.responses import ResponsesProtocol


class CodexAgent(CodexConfig):
    """Agent backed by the OpenAI Codex SDK."""

    def __init__(
        self,
        *,
        model: str | None = None,
        timeout_s: int | None = 10800,
        reasoning_effort: str = "max",
        cwd: str | None = None,
        api_key: str | None = None,
        mcp_timeout_s: int | None = 10800,
        mcp_parallel_dispatch: bool = False,
        base_prompt: str = "robot",
        web_search: str | None = None,
        agent_python: str | None = None,
    ):
        if reasoning_effort not in REASONING_EFFORTS:
            raise ValueError(f"unsupported reasoning effort: {reasoning_effort}")
        if base_prompt not in BASE_PROMPTS:
            raise ValueError(f"unsupported Codex base prompt: {base_prompt}")
        self._model = model
        self._timeout_s = timeout_s
        self._deadline = None if timeout_s is None else time.monotonic() + timeout_s
        self._reasoning_effort = reasoning_effort
        self._cwd = cwd or "."
        self._api_key = api_key
        self._mcp_timeout_s = mcp_timeout_s
        self._mcp_parallel_dispatch = mcp_parallel_dispatch
        self._base_prompt = base_prompt
        self._web_search = web_search
        self._agent_python = validate_agent_python(agent_python)
        self._sdk_events = CodexAgentEventAdapter()
        self._state: dict[str, Any] = {}
        self._worker: threading.Thread | None = None
        self._cancel_reason: str | None = None
        self._mcp_server = None
        self._instructions_dir: TemporaryDirectory | None = None

    capabilities = AgentCapabilities(tool_event_source="agent")


    def model_runtime(self, profile, event_bus=None):
        return ModelRuntime(
            "codex_responses", build_provider_adapter(profile, client_protocol=self.model_protocol), ResponsesProtocol(),
            self.responses_binding(event_bus), finalize_model_records,
        )

    def finalize_without_provider(self, result, session_dir):
        if session_dir is not None and result.trace_format == "codex_responses":
            result.train_error = "Codex trusted SFT export requires --provider-url so provider traffic can be recorded"

    def responses_binding(self, event_bus: EventBus | None = None):
        self._sdk_events = CodexAgentEventAdapter()
        return CodexResponsesBinding(event_bus=event_bus, sdk_events=self._sdk_events)

    def cancel(self, reason: str) -> None:
        self._cancel_reason = self._state.get("stop_reason") or reason
        self._state["stop_reason"] = self._cancel_reason
        self._interrupt(self._state)

    def close(self) -> None:
        worker = self._worker
        if worker is not None and worker.is_alive():
            self.cancel(self._cancel_reason or "cancelled")
            worker.join(timeout=15)
            if worker.is_alive():
                raise RuntimeError("Codex SDK worker did not stop during cleanup")
        self._worker = None
        if self._mcp_server is not None:
            self._mcp_server.stop()
            self._mcp_server = None
        if self._instructions_dir is not None:
            self._instructions_dir.cleanup()
            self._instructions_dir = None

    def run(
        self,
        *,
        prompt: Prompt,
        task: str,
        toolkit: Toolkit,
        executor: ToolExecutor,
        max_turns: int = 0,
        event_bus: EventBus | None = None,
        session_dir: Path | None = None,
        model_endpoint: str | None = None,
    ) -> AgentResult:
        """Run a Codex SDK session."""
        if self._deadline is not None and time.monotonic() >= self._deadline:
            return AgentResult(
                stop_reason="wall_time_limit",
                message="Episode deadline reached during startup",
            )
        if self._base_prompt == "robot" and not (prompt.system or "").strip():
            raise ValueError("robot base prompt requires a nonempty Prompt.system")
        try:
            import openai_codex
        except ImportError:
            raise RuntimeError("pip install openai-codex")

        from uni_robot_agent.services.mcp.http import HttpMcpServer

        if event_bus:
            event_bus.emit(
                AgentRunStarted(
                    instruction=task,
                    model=self._model or "",
                    agent_backend="codex",
                    tool_event_source=self.capabilities.tool_event_source,
                )
            )

        sdk_events = self._sdk_events
        self._mcp_server = mcp_server = HttpMcpServer(executor, event_bus=event_bus)
        mcp_url = mcp_server.start()
        logger.debug("MCP HTTP endpoint: %s", mcp_url)

        config_overrides = _sdk_config_overrides(
            mcp_url,
            self._reasoning_effort,
            self._mcp_timeout_s,
            mcp_parallel_dispatch=self._mcp_parallel_dispatch,
            web_search=self._web_search,
        )
        turn_input = prompt.flatten()
        if self._base_prompt == "robot":
            if session_dir is None:
                if self._instructions_dir is None:
                    self._instructions_dir = TemporaryDirectory(prefix="uni-robot-agent-codex-")
                instructions_dir = Path(self._instructions_dir.name)
            else:
                instructions_dir = Path(session_dir)
                instructions_dir.mkdir(parents=True, exist_ok=True)
            instructions = instructions_dir / "codex_base_instructions.md"
            instructions.write_text(prompt.system, encoding="utf-8")
            turn_input = prompt.user
            (instructions_dir / "codex_user_input.md").write_text(turn_input, encoding="utf-8")
            # Preserve assembled roles: system replaces SDK instructions; user contains only the task input.
            config_overrides.append(
                f"model_instructions_file={json.dumps(str(instructions.resolve()))}"
            )
        if model_endpoint:
            config_overrides.extend(
                [
                    'model_provider="uni_robot_agent_proxy"',
                    'model_providers.uni_robot_agent_proxy.name="uni_robot_agent_proxy"',
                    f'model_providers.uni_robot_agent_proxy.base_url="{model_endpoint}"',
                    'model_providers.uni_robot_agent_proxy.wire_api="responses"',
                    'model_providers.uni_robot_agent_proxy.env_key="UNI_ROBOT_AGENT_CODEX_KEY"',
                ]
            )

        env = {**os.environ}
        if self._agent_python is not None:
            python_bin = str(Path(self._agent_python).parent)
            env["PATH"] = python_bin + os.pathsep + env.get("PATH", os.defpath)
            # Profiles and snapshots can restore a different PATH after SDK startup.
            config_overrides.extend([
                f"shell_environment_policy.set.PATH={json.dumps(env['PATH'])}",
                "shell_environment_policy.experimental_use_profile=false",
                "allow_login_shell=false",
                "features.shell_snapshot=false",
            ])
        if session_dir is not None and model_endpoint:
            env["CODEX_HOME"] = str(Path(session_dir).resolve() / ".codex")
            Path(env["CODEX_HOME"]).mkdir(parents=True, exist_ok=True)
        if self._api_key:
            env["UNI_ROBOT_AGENT_CODEX_KEY"] = self._api_key

        codex_config = openai_codex.CodexConfig(
            config_overrides=tuple(config_overrides),
            cwd=self._cwd,
            env=env,
            # Avoid experimental app-server API fields to preserve provider compatibility.
            experimental_api=False,
        )

        turn_options = {
            "approval_mode": openai_codex.ApprovalMode.deny_all,
            "cwd": self._cwd,
            "model": self._model,
            "sandbox": openai_codex.Sandbox.full_access,
        }

        self._cancel_reason = None
        state = self._state = {}
        stopped = threading.Event()

        def run_worker():
            try:
                self._run_sdk_session(openai_codex, codex_config, turn_options,
                    turn_input, max_turns, state, event_bus, sdk_events)
            finally:
                stopped.set()

        worker = threading.Thread(target=run_worker, name="codex-sdk", daemon=True)

        self._worker = worker
        started = time.time()
        worker.start()
        error: str | None = None

        # Interrupting Thread.join can mark a live thread stopped on some Python versions.
        # Wait on explicit completion so cleanup never freezes records before the worker exits.
        interrupted = False
        try:
            stopped.wait(None if self._deadline is None else max(0.0, self._deadline - time.monotonic()))
        except KeyboardInterrupt:
            interrupted = True
            self.cancel("interrupted")
        if not stopped.is_set():
            if not interrupted:
                self.cancel("wall_time_limit")
            if not stopped.wait(timeout=15):
                error = "Codex SDK did not stop after cancellation"
        if error is None and "error" in state:
            error = str(state["error"])

        elapsed = time.time() - started
        texts = state.get("texts", [])
        turns_used = state.get("turns", 0)
        tool_calls = state.get("tool_calls", 0)
        steps = state.get("steps", 0)
        finish_request = state.get("finish_request")

        if finish_request:
            message = (
                finish_request.get("summary", "")
                or finish_request.get("message", "")
                or "\n".join(texts)
            )
        elif texts:
            message = "\n".join(texts)
        elif error:
            message = ""
        else:
            message = (
                "Codex session ended without output (no agent messages or tool calls)"
            )
            if tb := state.get("traceback"):
                logger.debug("traceback:\n%s", tb)

        success = finish_request is not None

        result = AgentResult(
            success=success,
            message=message,
            error=error,
            stop_reason=state.get("stop_reason"),
            turns_used=turns_used,
            tool_calls=tool_calls,
            steps_used=steps,
            elapsed_s=elapsed,
            main_thread_id=str(state.get("thread_id") or ""),
            trace_format="codex_responses",
            traceback=state.get("traceback"),
        )
        if interrupted:
            result.success = False
            result.stop_reason = "interrupted"
            result.error = "Interrupted by user (Ctrl-C)"
            raise AgentInterrupted(result)
        return result

    @staticmethod
    def _run_sdk_session(
        openai_codex: Any,
        config: Any,
        turn_options: dict,
        turn_input: str,
        max_turns: int,
        state: dict[str, Any],
        event_bus: EventBus | None,
        sdk_events: CodexAgentEventAdapter | None = None,
    ) -> None:
        """Run the Codex session in a worker thread."""
        bus = event_bus
        sdk_events = sdk_events or CodexAgentEventAdapter()

        try:
            # Update shared counts as events arrive so result.json preserves completed turns
            # and calls even when the session is interrupted or exits with an error.
            state.update(
                texts=[],
                turns=0,
                tool_calls=0,
                steps=0,
                finish_request=None,
                pending_reasoning=False,
            )
            texts = state["texts"]

            with openai_codex.Codex(config=config) as codex:
                state["codex"] = codex
                thread = codex.thread_start(**turn_options)
                state["thread"] = thread
                state["thread_id"] = str(thread.id)
                main_activity = sdk_events.set_main_agent(state["thread_id"])
                if bus:
                    bus.emit(main_activity)

                source = (
                    CodexEventSource(
                        codex,
                        openai_codex.__version__,
                        bus,
                        sdk_events,
                    )
                    if bus
                    else nullcontext()
                )
                with source:
                    turn = thread.turn(turn_input, **turn_options)
                    state["turn"] = turn
                    _consume_main_turn(
                        turn,
                        state,
                        texts,
                        max_turns,
                        bus,
                        sdk_events,
                    )
        except Exception as e:
            # Interrupting an SDK stream can raise while unwinding the expected stop.
            if state.get("stop_reason") in {"max_turns", "wall_time_limit"}:
                return
            import traceback

            state["error"] = f"{type(e).__name__}: {e}"
            state["traceback"] = traceback.format_exc()
            logger.error("codex session error: %s", state["error"])

    @staticmethod
    def _interrupt(state: dict[str, Any]) -> None:
        if turn := state.get("turn"):
            try:
                turn.interrupt()
            except Exception:
                pass
        if codex := state.get("codex"):
            try:
                codex.close()
            except Exception:
                pass


def _consume_main_turn(
    turn: Any,
    state: dict[str, Any],
    texts: list[str],
    max_turns: int,
    bus: EventBus | None,
    sdk_events: CodexAgentEventAdapter,
) -> None:
    """Consume the main turn; CodexEventSource publishes agent activity."""
    for event in turn.stream():
        method = _get(event, "method", "")
        payload = _get(event, "payload")
        logger.debug(
            "codex event: method=%s payload=%s",
            method,
            _summarize(payload),
        )

        if method == "turn/completed":
            if state["pending_reasoning"]:
                logger.warning(
                    "Codex turn completed with reasoning only; "
                    "no assistant message or tool call followed"
                )
            return
        if method != "item/completed":
            continue

        item = _unwrap(_get(payload, "item"))
        item_type = str(_get(item, "type", ""))
        agent_id = sdk_events.agent_id_from_payload(payload)
        if not sdk_events.is_main(agent_id):
            continue

        if item_type == "reasoning":
            state["pending_reasoning"] = True
            if bus and (text := _reasoning_summary(item)):
                bus.emit(ReasoningSummary(text=text))
            continue

        if item_type == "agentMessage":
            if max_turns > 0 and state["turns"] >= max_turns:
                state["stop_reason"] = "max_turns"
                turn.interrupt()
                return
            text = str(_get(item, "text", "")).strip()
            if text:
                state["pending_reasoning"] = False
                texts.append(text)
                state["turns"] += 1
                state["steps"] += 1
                if bus:
                    bus.emit(
                        AssistantText(
                            text=text,
                            agent_step=state["steps"],
                            turn=state["turns"],
                        )
                    )
            continue

        if item_type not in {
            "mcpToolCall",
            "dynamicToolCall",
            "commandExecution",
            "fileChange",
        }:
            continue

        state["pending_reasoning"] = False
        state["tool_calls"] += 1
        state["steps"] += 1
        if item_type in {"mcpToolCall", "dynamicToolCall"}:
            name = str(_get(item, "tool", ""))
            if _is_finish_tool(name):
                args = _get(item, "arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}
                state["finish_request"] = args if isinstance(args, dict) else {}
                # Return the actual finish result to the model and let its final response end the turn.
                continue


# Helpers


def _summarize(value: Any, max_len: int = 200) -> str:
    """Condense a payload into a readable debug string."""
    try:
        s = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        s = repr(value)
    return s[:max_len] + "..." if len(s) > max_len else s


def _is_finish_tool(name: str) -> bool:
    normalized = name.lower()
    return normalized == "finish" or normalized.endswith("__finish")


def _reasoning_summary(item: Any) -> str:
    """Extract the public summary from an SDK reasoning item."""
    value = _get(item, "summary") or _get(item, "content")
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, (list, tuple)):
        return ""

    parts = []
    for block in value:
        text = _get(_unwrap(block), "text", "")
        if text := str(text).strip():
            parts.append(text)
    return "\n\n".join(parts)


def _sdk_config_overrides(
    mcp_url: str,
    reasoning_effort: str,
    mcp_timeout_s: int | None = None,
    *,
    mcp_parallel_dispatch: bool = False,
    web_search: str | None = None,
) -> list[str]:
    """Return provider-independent Codex runtime configuration."""
    overrides = [
        f'mcp_servers.uni_robot_agent.url="{mcp_url}"',
        # Control only Codex dispatch; model batching and background execution have separate owners.
        f"mcp_servers.uni_robot_agent.supports_parallel_tool_calls={str(mcp_parallel_dispatch).lower()}",
        f'model_reasoning_effort="{reasoning_effort}"',
    ]
    if web_search is not None:
        overrides.append(f"web_search={json.dumps(web_search)}")
    if mcp_timeout_s is not None:
        if mcp_timeout_s <= 0:
            raise ValueError("mcp_timeout_s must be positive")
        overrides.append(f"mcp_servers.uni_robot_agent.tool_timeout_sec={mcp_timeout_s}")
    return overrides
