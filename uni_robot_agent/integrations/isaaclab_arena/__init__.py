"""IsaacLab Arena episodes composed from shared scene and hybrid tools."""
from typing import Literal

from uni_robot_agent.integrations.base import IntegrationSpec
from uni_robot_agent.integrations.hybrid import HybridOptions, add_hybrid_cli_args, validate_hybrid_cli


class ArenaOptions(HybridOptions):
    action_profile: Literal["hybrid"] | None = None
    run_id: str | None = None
    task: str | None = None
    seed: int | None = None


def add_cli_args(parser):
    group = parser.add_argument_group("IsaacLab Arena episode identity")
    group.add_argument("--run-id")
    group.add_argument("--task")
    group.add_argument("--seed", type=int)
    add_hybrid_cli_args(parser)


def validate_cli(args):
    if "hybrid" not in args.tool_modules:
        raise ValueError("IsaacLab Arena requires hybrid tools")
    validate_hybrid_cli(args)
    if args.action_model_binding not in (None, "arena_g1_locomanipulation"):
        raise ValueError("The admitted Arena embodiment uses arena_g1_locomanipulation")
    if args.sam3_url:
        raise ValueError("The Arena profile exposes RGB without SAM3 localization")


def get_integration_spec():
    from uni_robot_agent.integrations.episode_tools import EPISODE_MODULE, SCENE_MODULE, SKILLS_MODULE
    from uni_robot_agent.integrations.hybrid import HYBRID_MODULE
    from . import prompt  # noqa: F401 - register the integration prompt.
    from .session import create_session
    return IntegrationSpec(name="isaaclab_arena", create_session=create_session,
        add_cli_args=add_cli_args, validate_cli=validate_cli, config_model=ArenaOptions,
        tool_profiles={"hybrid": (SKILLS_MODULE, SCENE_MODULE, HYBRID_MODULE, EPISODE_MODULE)},
        default_tool_profile="hybrid", deployment_module="uni_robot_agent.integrations.isaaclab_arena.deployment",
        allow_automatic_retry=False)
