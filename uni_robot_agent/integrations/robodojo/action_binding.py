"""RoboDojo semantic policy over reusable hybrid arm capabilities."""
from uni_robot_agent.tools.hybrid.adapters.arm_gripper import (
    ArmActionLayout,
    ArmGripperBinding,
)

from .actor_profiles import profile_for, validate_x5_joint_profile

X5_JOINT_NAMES = tuple(f"joint{index}" for index in range(1, 7))


class ActionBinding(ArmGripperBinding):
    def __init__(self, args, description, actor_info, *, name="robodojo_pi05"):
        if name not in {"robodojo_pi05", "robodojo_x5_joint"}:
            raise ValueError(f"Unsupported RoboDojo action binding: {name}")
        environment = description.environment
        if (description.integration != "robodojo" or tuple(arm.name for arm in environment.arms) != ("left", "right")
                or environment.control_frequency_hz != 25):
            raise ValueError("RoboDojo binding requires the dual X5 environment at 25 Hz")
        for arm in environment.arms:
            control = arm.control
            if (tuple(arm.joint_names) != X5_JOINT_NAMES or arm.control_frame != "x5_grasp_site"
                    or control is None or set(control.commands) != {"joints", "pose"}
                    or control.pose_solver != "dls" or not control.joint_preview
                    or control.rotation != "full" or control.rotation_reference != "measured"
                    or control.gripper != "continuous"):
                raise ValueError("RoboDojo must publish the ordered X5 joints and grasp-site control contract")
        legacy = name == "robodojo_pi05"
        if legacy:
            if (actor_info.profile != profile_for(name) or actor_info.model != "pi05"
                    or actor_info.runtime != "openpi_jax" or actor_info.policy_rng_seed != 0
                    or actor_info.model_config_name != "pi05_base_aloha_full_sim_arx-x5_seed_0"):
                raise ValueError("Legacy RoboDojo Pi0.5 requires its identified profile and seed-zero runtime")
        else:
            # A compatible actor is independent of its serving backend.
            validate_x5_joint_profile(actor_info.profile)
        layout = {arm: ArmActionLayout(joints=tuple(f"{arm}_joint_{i}" for i in range(6)),
                                     gripper=f"{arm}_gripper") for arm in ("left", "right")}
        cameras = {"cam_high": "head", "cam_left_wrist": "left_wrist", "cam_right_wrist": "right_wrist"} if legacy else None
        super().__init__(name=name, description=description, actor_info=actor_info, action_layout=layout,
            observation_encoder=lambda state: [number for arm in ("left", "right")
                for number in (*state["arms"][arm]["joints"], state["arms"][arm]["gripper"])],
            camera_map=cameras, clip_grippers=legacy)


def create_binding(name, args, description, actor_info):
    return ActionBinding(args, description, actor_info, name=name)
