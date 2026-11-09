"""Shared actor configuration and lifecycle assembly for any tool family."""
from __future__ import annotations

import argparse
import re
from argparse import BooleanOptionalAction
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import Field

from uni_robot_agent.integrations.base import IntegrationOptions
from uni_robot_agent.integrations.composition import ToolModule
from uni_robot_agent.provider.registry import list_provider_profiles, validate_provider_profile
from uni_robot_agent.services.actor.client import ActorClient
from uni_robot_agent.tools.hybrid.provider import HybridTools, unavailable_tools, tool_policies
from uni_robot_agent.tools.hybrid.reviewer import ChunkReviewer


class HybridOptions(IntegrationOptions):
    action_profile: Literal["direct", "hybrid"] | None = None
    action_model_binding: str | None = None
    action_model_checkpoint_sha256: str | None = None
    vla_review: bool | None = None
    vla_review_model: str | None = None
    vla_review_base_url: str | None = None
    vla_review_adapter: str | None = None
    vla_review_api_key: str | None = Field(default=None, exclude=True, repr=False)


def add_hybrid_cli_args(parser: argparse.ArgumentParser) -> None:
    """Configure actor execution independently of the environment embodiment."""
    group = parser.add_argument_group("Hybrid actor")
    group.add_argument("--action-profile", choices=("direct", "hybrid"),
                       help="Compatibility alias for --tool-profile")
    group.add_argument("--action-model-binding",
                       help="Explicit actor binding; defaults to the environment's binding")
    group.add_argument("--action-model-checkpoint-sha256",
        dest="action_model_checkpoint_sha256",
    )
    group.add_argument("--vla-review",
        dest="vla_review",
        action=BooleanOptionalAction,
        default=None,
    )
    group.add_argument("--vla-review-model")
    group.add_argument("--vla-review-base-url")
    group.add_argument("--vla-review-adapter", choices=list_provider_profiles(client_protocol="responses"))
    group.add_argument("--vla-review-api-key", dest="_vla_review_api_key")


def validate_hybrid_cli(args: Any) -> None:
    """Validate actor and reviewer settings without opening a service."""
    binding = getattr(args, "action_model_binding", None)
    if binding and "hybrid" not in args.tool_modules:
        raise ValueError("--action-model-binding requires the hybrid action profile")
    if args.vla_review and "hybrid" not in args.tool_modules:
        raise ValueError("--vla-review requires the hybrid action profile")
    args.vla_review_model = args.vla_review_model or args.model
    # A separate reviewer endpoint/profile is resolved when the action binding
    # decides whether review is enabled. Messages endpoints cannot be inherited
    # merely because their bodies are passed through unchanged.
    if args.provider_profile in list_provider_profiles(client_protocol="responses"):
        args.vla_review_base_url = args.vla_review_base_url or args.provider_url
        args.vla_review_adapter = args.vla_review_adapter or args.provider_profile
    if args.vla_review_adapter:
        validate_provider_profile(args.vla_review_adapter, client_protocol="responses")
    if "hybrid" in args.tool_modules and args.vla_review is True:
        _validate_reviewer_endpoint(args)
    if args.vla_review_base_url:
        address = urlsplit(args.vla_review_base_url)
        if address.scheme not in {"http", "https"} or not address.hostname:
            raise ValueError("Reviewer endpoint must be an absolute HTTP(S) URL")
    if "hybrid" in args.tool_modules and not args.action_model_url:
        raise ValueError("Hybrid requires --action-model-url")
    checkpoint = getattr(args, "action_model_checkpoint_sha256", None)
    if "hybrid" in args.tool_modules and (not checkpoint or re.fullmatch(r"[0-9a-f]{64}", checkpoint) is None):
        raise ValueError("Hybrid requires --action-model-checkpoint-sha256 with 64 lowercase hex characters")
    if args.action_model_url:
        address = urlsplit(args.action_model_url)
        if (address.scheme not in {"http", "https"} or not address.hostname
                or address.query or address.fragment):
            raise ValueError("Actor endpoint must be an absolute HTTP(S) base URL")


def _validate_reviewer_endpoint(args):
    if not args.vla_review_model:
        raise ValueError("An enabled reviewer requires --vla-review-model or an agent model")
    if not args.vla_review_adapter or not args.vla_review_base_url:
        raise ValueError("An enabled reviewer requires a Responses-compatible "
                         "--vla-review-adapter and --vla-review-base-url; "
                         "a Messages planner endpoint cannot be inherited")
    validate_provider_profile(args.vla_review_adapter, client_protocol="responses")


def configure_hybrid(session):
    """Own services here; bindings only translate between declared contracts."""
    args, episode = session.args, session.episode
    binding_factory = session.binding_factory
    if binding_factory is None:
        raise ValueError("This integration has no hybrid binding factory")
    checkpoint = args.action_model_checkpoint_sha256
    if not checkpoint:
        raise ValueError("Hybrid requires --action-model-checkpoint-sha256")
    actor = session.own(ActorClient(args.action_model_url,
        checkpoint_sha256=checkpoint, tracer=session.context.tracer))
    name = args.action_model_binding or getattr(episode.description.environment, "action_binding", None)
    binding = binding_factory(name, args, episode.description, actor.info)
    enabled = binding.reviewer_supported if args.vla_review is None else args.vla_review
    if enabled and not binding.reviewer_supported:
        raise ValueError("The selected binding does not support actor review")
    if enabled:
        _validate_reviewer_endpoint(args)
    reviewer = session.own(ChunkReviewer(enabled=enabled,
        model=args.vla_review_model, base_url=args.vla_review_base_url,
        api_key=args._vla_review_api_key, adapter=args.vla_review_adapter,
        prompt_path=binding.reviewer_prompt, validator=binding.validate_decision,
        tracer=session.context.tracer))
    session.action_model_name = actor.info.model
    return HybridTools(episode, actor=actor, binding=binding, reviewer=reviewer)


HYBRID_MODULE = ToolModule("hybrid", configure_hybrid, tool_policies(),
    runtime_prompts=("hybrid.md",),
    declare_unavailable=lambda session: unavailable_tools(session.episode))
