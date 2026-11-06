"""DROID state semantics composed with the shared arm/gripper hybrid adapter."""

import math

from uni_robot_agent.services.actor.contracts import ActorField, ActorProfile

JOINT_NAMES = tuple(f"panda_joint{index}" for index in range(1, 8))
JOINT_FIELDS = tuple(f"right_joint_{index}" for index in range(7))


def droid_joint_profile(cameras, horizon, frequency_hz):
    """Measured finger angle and commanded physical gap are deliberately distinct."""
    joints = [ActorField(name=name, unit="rad", semantics="joint_position", frame="panda_joint")
              for name in JOINT_FIELDS]
    return ActorProfile(name="robolab_droid_joint", cameras=list(cameras),
        observation_fields=[*joints, ActorField(name="right_finger_joint", unit="normalized",
            semantics="finger_joint_position_over_pi_4", control_point="finger_joint")],
        action_fields=[*joints, ActorField(name="right_gripper", unit="normalized",
            semantics="normalized_opening", minimum=0, maximum=1)],
        action_horizon=horizon, control_frequency_hz=frequency_hz,
        image_convention="RGB HWC; model preprocessing belongs to the upstream adapter")


def encode_observation(state):
    arm = state["arms"]["right"]
    joints = arm["joints"]
    if len(joints) != 7:
        raise ValueError("DROID actor observations require seven measured joints")
    finger = arm["metadata"]["gripper_joint_pos_rad"]
    return [*joints, float(finger) / (math.pi / 4)]


def create_binding(name, args, description, actor_info):
    from uni_robot_agent.tools.hybrid.adapters.arm_gripper import ArmActionLayout, ArmGripperBinding

    environment = description.environment
    if name != "robolab_droid_joint" or description.integration != "robolab":
        raise ValueError("Select the robolab_droid_joint binding for a RoboLab episode")
    if len(environment.arms) != 1:
        raise ValueError("DROID requires exactly one arm")
    arm = environment.arms[0]
    if (arm.name != "right" or tuple(arm.joint_names) != JOINT_NAMES
            or arm.control_frame != "droid_robotiq_base_link"
            or arm.control is None or set(arm.control.commands) != {"joints", "pose"}
            or arm.control.pose_solver != "native" or not arm.control.joint_preview
            or arm.control.rotation != "full" or arm.control.rotation_reference != "measured"
            or arm.control.gripper != "continuous"):
        raise ValueError("RoboLab must publish the DROID joint and mounting-flange control contract")
    profile = actor_info.profile
    expected = droid_joint_profile(profile.cameras, profile.action_horizon, environment.control_frequency_hz)
    if profile != expected:
        raise ValueError("Actor must declare the complete DROID observation and physical-gap action profile")
    return ArmGripperBinding(name=name, description=description, actor_info=actor_info,
        action_layout={"right": ArmActionLayout(joints=JOINT_FIELDS, gripper="right_gripper")},
        observation_encoder=encode_observation, action_kind="joint")
