"""Codex model settings, runtime defaults and executable validation."""
from __future__ import annotations
from typing import Any
import os
from pathlib import Path
REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
BASE_PROMPTS = ("default", "robot")

def validate_agent_python(value: str | None) -> str | None:
    """Keep the venv path intact while validating the requested executable."""
    if value is None:
        return None
    if not value.strip():
        raise ValueError("--agent-python must name an executable Python interpreter")
    # Resolving symlinks would turn a venv interpreter into its base interpreter.
    path = Path(os.path.abspath(Path(value).expanduser()))
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ValueError(f"--agent-python is not an executable file: {path}")
    return str(path)


class CodexConfig:
    model_protocol = "responses"

    @classmethod
    def resolve_defaults(cls, args):
        if args.reasoning_effort is None:
            args.reasoning_effort = "max"
        if args.mcp_timeout_s is None:
            args.mcp_timeout_s = 10800

    @classmethod
    def add_cli_arguments(cls, parser) -> None:
        import argparse
        from uni_robot_agent.config import _positive

        options = parser.add_argument_group("Codex model and SDK")
        options.add_argument(
            "--agent-python",
            help="Python executable whose directory is prepended to Codex shell PATH; disables login shells and shell snapshots",
        )
        options.add_argument(
            "--codex-base-prompt", choices=BASE_PROMPTS, default="robot",
            help="Keep Codex built-in instructions or replace them with the assembled robot system prompt",
        )
        options.add_argument(
            "--codex-web-search", choices=("disabled", "cached", "live"),
            help="Override Codex native web search mode (default: SDK configuration)",
        )
        options.add_argument(
            "--mcp-parallel-dispatch",
            action=argparse.BooleanOptionalAction,
            default=False,
            help="Allow concurrent Codex MCP dispatch",
        )
        options.add_argument(
            "--mcp-timeout-s",
            type=_positive,
            help="Codex MCP acknowledgement timeout in seconds (default: 10800)",
        )
    @classmethod
    def validate_config(cls, args, explicit: set[str]) -> None:
        args.agent_python = validate_agent_python(args.agent_python)
        model_parallel_explicit = bool(
            explicit
            & {
                "--model-parallel-tool-calls",
                "--no-model-parallel-tool-calls",
            }
        )
        if model_parallel_explicit and not args.provider_url:
            raise ValueError(
                "model tool-call options require --provider-url or UNI_ROBOT_AGENT_PROVIDER_URL"
            )
        if (
            any(flag.startswith("--provider-") for flag in explicit)
            and not args.provider_url
        ):
            raise ValueError("provider request options require --provider-url")

    @classmethod
    def config_options(cls, args) -> dict[str, Any]:
        return {
            "reasoning_effort": args.reasoning_effort,
            "api_key": args._api_key,
            "mcp_timeout_s": args.mcp_timeout_s,
            "mcp_parallel_dispatch": args.mcp_parallel_dispatch,
            "base_prompt": args.codex_base_prompt,
            "web_search": args.codex_web_search,
            "agent_python": args.agent_python,
        }
