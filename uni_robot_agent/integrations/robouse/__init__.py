"""Compose RoboUse episodes with the shared gripper tool family."""

def get_integration_spec():
    from . import prompt
    from uni_robot_agent.integrations.gripper import build_gripper_spec, validate_gripper_cli

    def validate(args):
        validate_gripper_cli(args)
        if args.action_model_binding not in (None, "robouse_cartesian"):
            raise ValueError("RoboUse supports the robouse_cartesian binding for admitted controllers")

    spec = build_gripper_spec(name="robouse", create_session=create_session, validate_cli=validate)
    spec.deployment_module = "uni_robot_agent.integrations.robouse.deployment"
    return spec


def create_session(context):
    from uni_robot_agent.integrations.gripper import create_session as build
    from .action_binding import create_binding

    return build(context, binding_factory=create_binding)


def create_observation_profile(episode, session_dir):
    from uni_robot_agent.observation.episode import create_observation_profile
    return create_observation_profile(episode, session_dir)
