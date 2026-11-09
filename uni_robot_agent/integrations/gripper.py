"""Compose Direct gripper tools with independent scene and hybrid providers."""
from __future__ import annotations

import argparse
from dataclasses import replace
from typing import Any
from urllib.parse import urlsplit

from uni_robot_agent.integrations.base import IntegrationSpec
from uni_robot_agent.integrations.composition import ToolModule
from uni_robot_agent.integrations.episode_tools import EPISODE_MODULE, SCENE_MODULE, SKILLS_MODULE
from uni_robot_agent.integrations.hybrid import (
    HYBRID_MODULE, HybridOptions, add_hybrid_cli_args, validate_hybrid_cli,
)
from uni_robot_agent.integrations.prepared_episode import PreparedEpisodeSession
from uni_robot_agent.tools.scene.provider import SceneTools, tool_policies as scene_policies
from uni_robot_agent.tools.gripper.provider import GripperTools
from uni_robot_agent.tools.gripper import provider as gripper
from uni_robot_agent.tools.minimal import RESULT_NOTES, result_view


class GripperOptions(HybridOptions):
    run_id: str | None = None
    task: str | None = None


def add_gripper_cli_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("Gripper episode")
    group.add_argument("--run-id", help="Optional expected episode identity")
    group.add_argument("--task", help="Optional expected native task identity")
    add_hybrid_cli_args(parser)


def validate_gripper_cli(args: Any) -> None:
    validate_hybrid_cli(args)
    if "scene" in args.tool_modules and not args.no_sam3 and not args.sam3_url:
        raise ValueError("Set --sam3-url or explicitly use --no-sam3")
    if args.sam3_url:
        address = urlsplit(args.sam3_url)
        if address.scheme not in {"http", "https"} or not address.hostname or address.query or address.fragment:
            raise ValueError("Perception endpoint must be an absolute HTTP(S) base URL")


GRIPPER_MODULE = ToolModule("gripper", lambda session: GripperTools(session.episode), gripper.tool_policies())

MINIMAL_GRIPPER_MODULE = ToolModule("minimal_gripper",
    lambda session: GripperTools(session.episode, names=("move", "hold")),
    {name: policy for name, policy in gripper.tool_policies().items() if name in {"move", "hold"}},
    model_view=result_view, prompt_contributions=RESULT_NOTES)
MINIMAL_SCENE_MODULE = ToolModule("minimal_scene",
    lambda session: SceneTools(session.episode, names=("capture_scene",)),
    {"capture_scene": scene_policies()["capture_scene"]},
    model_view=result_view)
MINIMAL_TOOLS = (MINIMAL_GRIPPER_MODULE, MINIMAL_SCENE_MODULE,
                 replace(EPISODE_MODULE, model_view=result_view))

DIRECT_TOOLS = (SKILLS_MODULE, SCENE_MODULE, GRIPPER_MODULE, EPISODE_MODULE)
HYBRID_TOOLS = (SKILLS_MODULE, SCENE_MODULE, GRIPPER_MODULE, HYBRID_MODULE, EPISODE_MODULE)


def create_session(context, *, requirements_loader=None, binding_factory=None):
    return PreparedEpisodeSession(context, binding_factory=binding_factory,
                                  requirements_loader=requirements_loader)


def build_gripper_spec(*, name, create_session, tool_profiles,
                       add_cli_args=add_gripper_cli_args,
                       validate_cli=validate_gripper_cli, config_model=GripperOptions):
    return IntegrationSpec(name=name, create_session=create_session,
        add_cli_args=add_cli_args, tool_profiles=tool_profiles,
        debug_modules=(HYBRID_MODULE,), validate_cli=validate_cli, config_model=config_model)
