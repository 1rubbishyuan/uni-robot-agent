"""Compose Direct gripper tools with independent scene and hybrid providers."""
from __future__ import annotations

import argparse
from functools import partial
from typing import Any
from urllib.parse import urlsplit

from uni_robot_agent.integrations.base import IntegrationSpec
from uni_robot_agent.integrations.hybrid import (
    HybridOptions, add_hybrid_cli_args, configure_hybrid, validate_hybrid_cli,
)
from uni_robot_agent.integrations.prepared_episode import PreparedEpisodeSession
from uni_robot_agent.tools.gripper.provider import GripperTools
from uni_robot_agent.tools.scene.provider import SceneTools
from uni_robot_agent.tools.skills import SkillTools
from uni_robot_agent.tools.episode import EpisodeTools
from uni_robot_agent.tools.gripper import provider as gripper
from uni_robot_agent.tools.hybrid import provider as hybrid
from uni_robot_agent.tools.scene import provider as scene
from uni_robot_agent.tools import skills, episode

NAMES = (*skills.NAMES, *scene.NAMES, *gripper.NAMES, *hybrid.NAMES, *episode.NAMES)


class GripperOptions(HybridOptions):
    run_id: str | None = None
    task: str | None = None


def tool_policies():
    return {**skills.tool_policies(), **scene.tool_policies(),
            **gripper.tool_policies(), **hybrid.tool_policies(), **episode.tool_policies()}


def add_gripper_cli_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("Gripper episode")
    group.add_argument("--run-id", help="Optional expected episode identity")
    group.add_argument("--task", help="Optional expected native task identity")
    add_hybrid_cli_args(parser)


def validate_gripper_cli(args: Any) -> None:
    validate_hybrid_cli(args)
    if not args.no_sam3 and not args.sam3_url:
        raise ValueError("Set --sam3-url or explicitly use --no-sam3")
    if args.sam3_url:
        address = urlsplit(args.sam3_url)
        if address.scheme not in {"http", "https"} or not address.hostname or address.query or address.fragment:
            raise ValueError("Perception endpoint must be an absolute HTTP(S) base URL")


def configure_tools(session, *, binding_factory=None):
    runtime = session.episode
    perception = None
    if session.args.sam3_url:
        from uni_robot_agent.services.perception.client import PerceptionClient
        perception = session.own(PerceptionClient(session.args.sam3_url, tracer=session.context.tracer))
    hybrid_tools = configure_hybrid(session, binding_factory=binding_factory)
    return (SkillTools(runtime, writing=getattr(session.args, "skill_writing", False)),
            SceneTools(runtime, perception=perception), GripperTools(runtime),
            hybrid_tools, EpisodeTools(runtime))


def create_session(context, *, requirements_loader=None, binding_factory=None):
    return PreparedEpisodeSession(context,
        configure_tools=partial(configure_tools, binding_factory=binding_factory),
        requirements_loader=requirements_loader)


def build_gripper_spec(*, name, create_session, add_cli_args=add_gripper_cli_args,
                       validate_cli=validate_gripper_cli, config_model=GripperOptions):
    return IntegrationSpec(name=name, create_session=create_session,
        add_cli_args=add_cli_args, eval_tool_names=NAMES, tool_policies=tool_policies(),
        validate_cli=validate_cli, config_model=config_model)
