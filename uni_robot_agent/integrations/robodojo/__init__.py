"""RoboDojo entrypoints and environment-specific task requirements."""

from argparse import BooleanOptionalAction

from uni_robot_agent.integrations.gripper import GripperOptions


class RobodojoOptions(GripperOptions):
    include_extra_task_requirements: bool = True



def add_cli_args(parser):
    from uni_robot_agent.integrations.gripper import add_gripper_cli_args

    add_gripper_cli_args(parser)
    group = parser.add_argument_group("RoboDojo task instructions")
    group.add_argument(
        "--include-extra-task-requirements",
        action=BooleanOptionalAction,
        default=True,
        help="Append RoboDojo instruction_supplements.json guidance for the prepared task (default: enabled)",
    )


def validate_cli(args):
    from uni_robot_agent.integrations.gripper import validate_gripper_cli

    if args.vla_review is None:
        args.vla_review = "hybrid" in args.tool_modules
    validate_gripper_cli(args)


def create_session(context):
    from uni_robot_agent.integrations.gripper import create_session as build
    from uni_robot_agent.integrations.robodojo.prompt import load_task_requirements

    from uni_robot_agent.integrations.robodojo.action_binding import create_binding

    loader = load_task_requirements if context.args.include_extra_task_requirements else None
    return build(context, requirements_loader=loader, binding_factory=create_binding)


def get_integration_spec():
    from uni_robot_agent.integrations.gripper import DIRECT_TOOLS, HYBRID_TOOLS, build_gripper_spec

    spec = build_gripper_spec(name="robodojo", create_session=create_session,
                              tool_profiles={"direct": DIRECT_TOOLS, "hybrid": HYBRID_TOOLS},
                              add_cli_args=add_cli_args, validate_cli=validate_cli, config_model=RobodojoOptions)
    spec.deployment_module = "uni_robot_agent.integrations.robodojo.deployment"
    return spec


def create_observation_profile(episode, session_dir):
    from uni_robot_agent.context.prompt import get_prompts_dir
    from uni_robot_agent.observation.episode import create_observation_profile as build

    return build(episode, session_dir,
                 extra_guides=(get_prompts_dir() / "observer/robodojo_main.md",))
