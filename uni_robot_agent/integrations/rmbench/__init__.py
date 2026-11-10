"""RMBench memory-dependent manipulation with shared Direct gripper tools."""


def get_integration_spec():
    from uni_robot_agent.integrations.gripper import CLEAN_TOOLS, DIRECT_TOOLS
    from uni_robot_agent.integrations.simulation import build_simulation_spec
    return build_simulation_spec("rmbench", tool_profiles={"clean": CLEAN_TOOLS, "direct": DIRECT_TOOLS})
