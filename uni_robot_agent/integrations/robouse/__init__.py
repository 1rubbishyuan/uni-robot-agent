"""Compose RoboUse episodes with the shared gripper tool family."""

def get_integration_spec():
    from . import prompt
    from uni_robot_agent.integrations.gripper import build_gripper_spec, create_session, validate_gripper_cli

    def validate(args):
        validate_gripper_cli(args)
        if args.action_profile != "direct":
            raise ValueError("RoboUse requires --action-profile direct; no actor binding is registered")

    spec = build_gripper_spec(name="robouse", create_session=create_session, validate_cli=validate)
    spec.deployment_module = "uni_robot_agent.integrations.robouse.deployment"
    return spec


def create_observation_profile(episode, session_dir):
    from uni_robot_agent.observation.episode import create_observation_profile
    return create_observation_profile(episode, session_dir)
