"""Explicit public Cartesian profiles for reviewed RoboUse controllers."""

from uni_robot_agent.services.actor.contracts import ActorField, ActorProfile


def _names(arm, component, axes):
    return tuple(f"{arm}_{component}_{axis}" for axis in axes)


def _pose_fields(arm):
    common = {"frame": "world", "control_point": arm.control_frame}
    return [*(ActorField(name=name, unit="m", semantics="position", **common)
              for name in _names(arm.name, "position", "xyz")),
            *(ActorField(name=name, unit="unitless", semantics="quaternion_wxyz", **common)
              for name in _names(arm.name, "quaternion", "wxyz")),
            ActorField(name=f"{arm.name}_gripper", unit="normalized", semantics="normalized_opening",
                       minimum=0, maximum=1)]


def cartesian_profile(environment, cameras, horizon, *, delta=False):
    """Public control-point poses; deltas use each latest acknowledged measured pose."""
    observations, actions = [], []
    for arm in environment.arms:
        observations.extend(_pose_fields(arm))
        if delta:
            common = {"frame": "world", "control_point": arm.control_frame, "reference": "latest_ack"}
            actions.extend(ActorField(name=name, unit="m", semantics="translation_delta", **common)
                           for name in _names(arm.name, "delta_position", "xyz"))
            if arm.control.rotation == "full":
                actions.extend(ActorField(name=name, unit="rad", semantics="rotation_vector_delta", **common)
                               for name in _names(arm.name, "delta_rotation", "xyz"))
            actions.append(_pose_fields(arm)[-1])
        else:
            fields = _pose_fields(arm)
            actions.extend(fields if arm.control.rotation == "full" else [*fields[:3], fields[-1]])
    return ActorProfile(name="robouse_cartesian_delta" if delta else "robouse_cartesian_absolute",
        cameras=list(cameras), observation_fields=observations, action_fields=actions,
        action_horizon=horizon, control_frequency_hz=environment.control_frequency_hz,
        image_convention="RGB HWC; model preprocessing belongs to the upstream adapter")


def create_binding(name, args, description, actor_info):
    from uni_robot_agent.tools.hybrid.adapters.arm_gripper import ArmActionLayout, ArmGripperBinding

    if name != "robouse_cartesian" or description.integration != "robouse":
        raise ValueError("Select robouse_cartesian for an admitted RoboUse controller")
    environment = description.environment
    for arm in environment.arms:
        control = arm.control
        if (control is None or control.commands != ["pose"] or control.pose_solver != "native"
                or control.gripper != "continuous" or control.joint_preview):
            raise ValueError("RoboUse hybrid requires an explicitly admitted public Cartesian controller")
        if arm.control_frame == "panda_fingerpad_midpoint":
            valid = control.rotation == "full" and control.rotation_reference == "measured"
        elif arm.control_frame == "robocasa_tcp":
            valid = control.rotation == "fixed" and control.rotation_reference == "target"
        else:
            valid = False
        if not valid:
            raise ValueError("RoboUse control point and rotation capabilities are incompatible")
    profile = actor_info.profile
    delta = profile.name == "robouse_cartesian_delta"
    if profile != cartesian_profile(environment, profile.cameras, profile.action_horizon, delta=delta):
        raise ValueError("Actor must declare the complete RoboUse public Cartesian profile")

    def encode_observation(state):
        result = []
        for descriptor in environment.arms:
            measured = state["arms"][descriptor.name]
            result.extend([*measured["control_pose"]["position"],
                           *measured["control_pose"]["quaternion"], measured["gripper"]])
        return result

    layouts = {}
    for arm in environment.arms:
        layouts[arm.name] = ArmActionLayout(
            position=_names(arm.name, "delta_position" if delta else "position", "xyz"),
            quaternion=_names(arm.name, "quaternion", "wxyz") if not delta and arm.control.rotation == "full" else (),
            rotation_vector=_names(arm.name, "delta_rotation", "xyz") if delta and arm.control.rotation == "full" else (),
            gripper=f"{arm.name}_gripper")
    return ArmGripperBinding(name=name, description=description, actor_info=actor_info,
        action_layout=layouts, observation_encoder=encode_observation,
        action_kind="cartesian_delta" if delta else "cartesian_absolute",
        delta_reference="latest_ack" if delta else None)
