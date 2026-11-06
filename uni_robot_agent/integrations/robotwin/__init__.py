"""RoboTwin 2.0 with the shared Direct dual-arm gripper tools."""


def get_integration_spec():
    from uni_robot_agent.integrations.simulation import direct_spec
    return direct_spec("robotwin")
