"""LIBERO-PRO through the shared Direct gripper tools."""


def get_integration_spec():
    from uni_robot_agent.integrations.gripper import DIRECT_TOOLS
    from uni_robot_agent.integrations.simulation import build_simulation_spec
    return build_simulation_spec("libero_pro", tool_profiles={"direct": DIRECT_TOOLS})
