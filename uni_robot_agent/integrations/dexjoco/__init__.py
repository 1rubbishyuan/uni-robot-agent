"""DexJoCo sessions with scene and device-independent hybrid tools."""
from typing import Literal

from uni_robot_agent.integrations.base import IntegrationSpec
from uni_robot_agent.integrations.hybrid import (
    HybridOptions,
    add_hybrid_cli_args,
    validate_hybrid_cli,
)


class DexjocoOptions(HybridOptions):
    action_profile: Literal["hybrid"] = "hybrid"
    run_id: str | None = None
    task: str | None = None
    seed: int | None = None


def add_cli_args(parser):
    group = parser.add_argument_group("DexJoCo episode identity")
    group.add_argument("--run-id", help="Optional expected prepared episode identity")
    group.add_argument("--task", help="Optional expected DexJoCo task name")
    group.add_argument("--seed", type=int, help="Optional expected native constructor seed")
    add_hybrid_cli_args(parser)
    parser.set_defaults(action_profile="hybrid")


def validate_cli(args):
    if args.action_profile != "hybrid":
        raise ValueError("DexJoCo requires hybrid; Direct motion is not available")
    validate_hybrid_cli(args)
    if args.action_model_binding not in (None, "dexjoco_tcp_hand"):
        raise ValueError("DexJoCo supports the dexjoco_tcp_hand binding")
    if args.sam3_url:
        raise ValueError("This DexJoCo profile provides RGB observations without SAM3 localization")


def get_integration_spec():
    from uni_robot_agent.tools import episode, skills
    from uni_robot_agent.tools.hybrid import provider as hybrid
    from uni_robot_agent.tools.scene import provider as scene

    from . import prompt  # noqa: F401 - register the integration prompt.
    from .session import create_session
    return IntegrationSpec(name="dexjoco", create_session=create_session,
        add_cli_args=add_cli_args, validate_cli=validate_cli, config_model=DexjocoOptions,
        eval_tool_names=(*skills.NAMES, *scene.NAMES, *hybrid.NAMES, *episode.NAMES),
        tool_policies={**skills.tool_policies(), **scene.tool_policies(),
                       **hybrid.tool_policies(), **episode.tool_policies()},
        deployment_module="uni_robot_agent.integrations.dexjoco.deployment", allow_automatic_retry=False)
