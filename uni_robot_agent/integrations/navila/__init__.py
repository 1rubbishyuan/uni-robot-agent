"""NaVILA-Bench Go2 navigation through shared observation and Hybrid tools."""
from typing import Literal

from uni_robot_agent.integrations.base import IntegrationSpec
from uni_robot_agent.integrations.hybrid import HybridOptions, add_hybrid_cli_args, validate_hybrid_cli


class NaVILAOptions(HybridOptions):
    action_profile: Literal["hybrid"] | None = None
    run_id: str | None = None
    task: str | None = None
    seed: int | None = None


def add_cli_args(parser):
    group = parser.add_argument_group("NaVILA prepared episode")
    group.add_argument("--run-id")
    group.add_argument("--task", help="Expected episode:<zero-based dataset index>")
    group.add_argument("--seed", type=int)
    add_hybrid_cli_args(parser)


def validate_cli(args):
    if "hybrid" not in args.tool_modules:
        raise ValueError("NaVILA requires the Hybrid tool family")
    validate_hybrid_cli(args)
    if args.action_model_binding not in (None, "navila_go2"):
        raise ValueError("NaVILA supports the navila_go2 binding")
    if args.sam3_url:
        raise ValueError("NaVILA provides RGB navigation without a SAM3 binding")


def get_integration_spec():
    from uni_robot_agent.integrations.episode_tools import EPISODE_MODULE, SCENE_MODULE, SKILLS_MODULE
    from uni_robot_agent.integrations.hybrid import HYBRID_MODULE
    from . import prompt  # noqa: F401
    from .session import create_session
    return IntegrationSpec(name="navila", create_session=create_session, add_cli_args=add_cli_args,
        validate_cli=validate_cli, config_model=NaVILAOptions,
        tool_profiles={"hybrid": (SKILLS_MODULE, SCENE_MODULE, HYBRID_MODULE, EPISODE_MODULE)},
        default_tool_profile="hybrid", deployment_module="uni_robot_agent.integrations.navila.deployment",
        allow_automatic_retry=False)
