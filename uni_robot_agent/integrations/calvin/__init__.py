"""CALVIN single-task episodes with the shared Direct gripper tools."""


def get_integration_spec():
    from uni_robot_agent.integrations.simulation import direct_spec
    return direct_spec("calvin")
