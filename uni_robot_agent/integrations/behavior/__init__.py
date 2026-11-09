"""R1Pro mobile manipulation through the shared Hybrid tool family."""
from typing import Literal

from uni_robot_agent.integrations.base import IntegrationSpec
from uni_robot_agent.integrations.hybrid import HybridOptions, add_hybrid_cli_args, validate_hybrid_cli


class BehaviorOptions(HybridOptions):
    action_profile: Literal["hybrid"] | None = None
    task: str | None = None
    run_id: str | None = None
    seed: int | None = None


def add_cli_args(parser):
    group = parser.add_argument_group("BEHAVIOR episode identity")
    group.add_argument("--task", help="Expected prepared BEHAVIOR task")
    group.add_argument("--run-id", help="Expected prepared episode identity")
    group.add_argument("--seed", type=int, help="Expected native reset seed")
    add_hybrid_cli_args(parser)


def validate_cli(args):
    if "hybrid" not in args.tool_modules:
        raise ValueError("BEHAVIOR R1Pro requires the Hybrid tool profile")
    validate_hybrid_cli(args)
    if args.action_model_binding not in (None, "behavior_r1pro_joint"):
        raise ValueError("BEHAVIOR R1Pro requires behavior_r1pro_joint")
    if args.sam3_url:
        raise ValueError("This BEHAVIOR profile exposes RGB without geometric localization")


def get_integration_spec():
    from uni_robot_agent.integrations.episode_tools import EPISODE_MODULE, SCENE_MODULE, SKILLS_MODULE
    from uni_robot_agent.integrations.hybrid import HYBRID_MODULE
    from . import prompt  # noqa: F401 - register the integration prompt.
    from .session import create_session
    return IntegrationSpec(name="behavior", create_session=create_session, config_model=BehaviorOptions,
        add_cli_args=add_cli_args, validate_cli=validate_cli,
        tool_profiles={"hybrid": (SKILLS_MODULE, SCENE_MODULE, HYBRID_MODULE, EPISODE_MODULE)},
        default_tool_profile="hybrid", deployment_module="uni_robot_agent.integrations.behavior.deployment",
        allow_automatic_retry=False)
