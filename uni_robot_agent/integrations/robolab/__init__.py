"""RoboLab episodes composed from the shared gripper tools."""

from dataclasses import replace

from uni_robot_agent.integrations.gripper import GripperOptions


class RobolabOptions(GripperOptions):
    seed: int = 0
    instruction_type: str = "default"



def add_cli_args(parser):
    from uni_robot_agent.integrations.gripper import add_gripper_cli_args
    add_gripper_cli_args(parser)
    group = parser.add_argument_group("RoboLab episode identity")
    group.add_argument("--seed", type=int, default=0)
    group.add_argument("--instruction-type", default="default")


def validate_cli(args):
    from uni_robot_agent.integrations.gripper import validate_gripper_cli
    validate_gripper_cli(args)
    if not args.task:
        raise ValueError("RoboLab requires --task with the expected native task class")
    if args.action_model_binding not in (None, "robolab_droid_joint"):
        raise ValueError("RoboLab supports the robolab_droid_joint binding")
    if args.sam3_url:
        raise ValueError("This RoboLab profile requires --no-sam3")


def get_integration_spec():
    from . import prompt  # Register environment context without loading Isaac.
    from .session import create_session
    from uni_robot_agent.integrations.gripper import DIRECT_TOOLS, HYBRID_TOOLS, build_gripper_spec
    return replace(build_gripper_spec(name="robolab", create_session=create_session,
                              tool_profiles={"direct": DIRECT_TOOLS, "hybrid": HYBRID_TOOLS},
                                     add_cli_args=add_cli_args, validate_cli=validate_cli, config_model=RobolabOptions),
                   allow_automatic_retry=False)
