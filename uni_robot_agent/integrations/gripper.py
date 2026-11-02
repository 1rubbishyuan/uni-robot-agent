"""Select the gripper tool family and its environment/model bindings."""
from __future__ import annotations

import argparse
from argparse import BooleanOptionalAction
from typing import Any, Literal

from pydantic import Field
from urllib.parse import urlsplit

from uni_robot_agent.integrations.base import IntegrationSpec, IntegrationOptions
from uni_robot_agent.provider.registry import list_provider_profiles, validate_provider_profile
from uni_robot_agent.integrations.prepared_episode import PreparedEpisodeSession
from uni_robot_agent.tools.gripper.reviewer import ChunkReviewer
from uni_robot_agent.tools.gripper.provider import GripperTools
from uni_robot_agent.tools.scene.provider import SceneTools
from uni_robot_agent.tools.skills import SkillTools
from uni_robot_agent.tools.episode import EpisodeTools
from uni_robot_agent.tools.gripper import provider as gripper
from uni_robot_agent.tools.scene import provider as scene
from uni_robot_agent.tools import skills, episode

NAMES = (*skills.NAMES, *scene.NAMES, *gripper.NAMES, *episode.NAMES)


class GripperOptions(IntegrationOptions):
    run_id: str | None = None
    task: str | None = None
    action_profile: Literal["direct", "hybrid"] = "direct"
    action_model_checkpoint_sha256: str | None = None
    vla_review: bool | None = None
    vla_review_model: str | None = None
    vla_review_base_url: str | None = None
    vla_review_adapter: str | None = None
    vla_review_api_key: str | None = Field(default=None, exclude=True, repr=False)


def tool_policies():
    return {**skills.tool_policies(), **scene.tool_policies(),
            **gripper.tool_policies(), **episode.tool_policies()}


def add_gripper_cli_args(parser: argparse.ArgumentParser) -> None:
    """Configure gripper episodes and their optional model endpoints."""
    group = parser.add_argument_group("Gripper episode")
    group.add_argument("--run-id", help="Optional expected episode identity")
    group.add_argument("--task", help="Optional expected native task identity")
    group.add_argument("--action-profile", choices=("direct", "hybrid"), default="direct")
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

def validate_gripper_cli(args: Any) -> None:
    """Validate shared actor, reviewer, and perception endpoint settings."""
    if args.vla_review and args.action_profile != "hybrid":
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
    if args.action_profile == "hybrid" and args.vla_review is True:
        _validate_reviewer_endpoint(args)
    if args.vla_review_base_url:
        address = urlsplit(args.vla_review_base_url)
        if address.scheme not in {"http", "https"} or not address.hostname:
            raise ValueError("Reviewer endpoint must be an absolute HTTP(S) URL")
    if not args.no_sam3 and not args.sam3_url:
        raise ValueError("Set --sam3-url or explicitly use --no-sam3")
    if args.action_profile == "hybrid" and not args.action_model_url:
        raise ValueError("Hybrid requires --action-model-url")
    for name, endpoint in (("Actor", args.action_model_url), ("Perception", args.sam3_url)):
        if endpoint:
            address = urlsplit(endpoint)
            if (
                address.scheme not in {"http", "https"}
                or not address.hostname
                or address.query
                or address.fragment
            ):
                raise ValueError(f"{name} endpoint must be an absolute HTTP(S) base URL")

def _validate_reviewer_endpoint(args):
    if not args.vla_review_model:
        raise ValueError("An enabled reviewer requires --vla-review-model or an agent model")
    if not args.vla_review_adapter or not args.vla_review_base_url:
        raise ValueError("An enabled reviewer requires a Responses-compatible "
                         "--vla-review-adapter and --vla-review-base-url; "
                         "a Messages planner endpoint cannot be inherited")
    validate_provider_profile(args.vla_review_adapter, client_protocol="responses")


def create_binding(name, args, description):
    if name == "robodojo_pi05":
        from uni_robot_agent.integrations.robodojo.action_binding import ActionBinding
    else:
        raise ValueError(f"No registered action binding: {name!r}")
    return ActionBinding(args, description)


def configure_tools(session, *, binding_factory=create_binding):
    args, runtime = session.args, session.episode
    perception = binding = reviewer = None
    if args.sam3_url:
        from uni_robot_agent.services.perception.client import PerceptionClient
        perception = session.own(PerceptionClient(args.sam3_url, tracer=session.context.tracer))
    if args.action_profile == "hybrid":
        binding = session.own(binding_factory(runtime.description.environment.action_binding, args, runtime.description))
        enabled = getattr(args, "vla_review", None)
        enabled = binding.reviewer_supported if enabled is None else enabled
        if enabled and not binding.reviewer_supported:
            raise ValueError("The selected binding does not support reviewer corrections")
        if enabled:
            _validate_reviewer_endpoint(args)
        reviewer = session.own(ChunkReviewer(enabled=enabled,
            model=args.vla_review_model, base_url=args.vla_review_base_url,
            api_key=args._vla_review_api_key, adapter=args.vla_review_adapter,
            prompt_path=binding.reviewer_prompt,
            validator=binding.validate_decision, tracer=session.context.tracer))
        session.action_model_name = binding.name
    return (SkillTools(runtime, writing=getattr(args, "skill_writing", False)), SceneTools(runtime, perception=perception),
            GripperTools(runtime, action_profile=args.action_profile, binding=binding, reviewer=reviewer), EpisodeTools(runtime))


def create_session(context, *, requirements_loader=None):
    return PreparedEpisodeSession(context, configure_tools=configure_tools,
                                  requirements_loader=requirements_loader)


def build_gripper_spec(*, name, create_session, add_cli_args=add_gripper_cli_args,
                       validate_cli=validate_gripper_cli, config_model=GripperOptions):
    return IntegrationSpec(name=name, create_session=create_session,
        add_cli_args=add_cli_args, eval_tool_names=NAMES, tool_policies=tool_policies(),
        validate_cli=validate_cli, config_model=config_model)
