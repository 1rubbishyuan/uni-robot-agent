"""Robot-only nominal DROID trajectories; no simulator or live controller writes."""

import numpy as np

from .gripper import MAX_OPENING_M, NATIVE_ANGLE_SCALE
from .kinematics import feedback, pose, pose_fields


def _target(model, root, joints, opening, index):
    fields = pose_fields(root @ model.fk(np.asarray(joints)))
    return {"right": {"index": index, "position": fields["position"],
        "quaternion_wxyz": fields["quaternion"], "gripper_opening": float(opening),
        "gripper_closed": bool(opening < .5)}}


def preview_joints(controller, measured, commands):
    """FK of requests and slew-limited drives, assuming perfect subsequent tracking."""
    model = controller.robot
    root = pose(measured["root_position"], measured["root_quaternion"])
    check = feedback(root @ model.fk(np.asarray(measured["joints"])),
                     pose(measured["position"], measured["quaternion"]))
    if not check["target_reached"]:
        raise ValueError("DROID preview FK disagrees with the measured mounting flange")
    shadow = controller.snapshot()
    nominal = dict(measured)
    trajectory, requested, applied = [], [], []
    for index, command in enumerate(commands):
        target = command.arms["right"]
        row = shadow.bounded_target(target.joints, target.gripper, nominal)
        opening = float(np.clip(np.interp(row[7], model.gripper.commands,
                                         model.gripper.widths) / MAX_OPENING_M, 0, 1))
        nominal_target = _target(model, root, row[:7], target.gripper, index)
        nominal_target["right"]["gripper_applied_opening"] = opening
        trajectory.append(nominal_target)
        requested.append(_target(model, root, target.joints, target.gripper, index))
        applied.append({"right": {"joints": row[:7].tolist(), "finger_joint_rad": float(row[7] * NATIVE_ANGLE_SCALE)}})
        shadow.acknowledge(row)
        nominal = {**nominal, "joints": row[:7].tolist(), "finger_joint_rad": float(row[7] * NATIVE_ANGLE_SCALE)}
    return {"trajectory": trajectory, "requested_trajectory": requested,
        "applied_targets": applied, "measured_fk_check": {"right": check},
        "interpretation": "World-frame Robotiq mounting-flange FK of nominal slew-limited drive targets. "
            "Subsequent samples assume perfect tracking. gripper_opening retains requested drive intent; "
            "gripper_applied_opening is the nominal limited drive gap. Requested targets are recorded separately. "
            "No physics, contact, robot self-clearance or scene collision prediction is performed; "
            "the native controller rechecks measured state and robot clearance before every real tick."}
