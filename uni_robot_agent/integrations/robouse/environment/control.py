"""Pose and aperture conversions, without motion loops."""
import numpy as np
from scipy.spatial.transform import Rotation
from uni_robot_agent.services.environment.contracts import ArmState, Pose, RequestRejected


def rotation(q):
    return Rotation.from_quat(np.asarray(q)[[1, 2, 3, 0]])


def quaternion(matrix):
    return Rotation.from_matrix(np.asarray(matrix).reshape(3, 3)).as_quat()[[3, 0, 1, 2]].tolist()


def state(position, quat, opening, command):
    return ArmState(control_pose=Pose(position=list(map(float, position)), quaternion=list(map(float, quat))),
                    gripper=float(np.clip(opening, 0, 1)), gripper_command=float(command))


def fixed_rotation(target, reference, tolerance=.03):
    if np.linalg.norm((rotation(target) * rotation(reference).inv()).as_rotvec()) > tolerance:
        raise RequestRejected("This backend holds orientation fixed; preserve its initial quaternion")


def axis_angle(target, reference, axis, limit):
    vector = (rotation(target) * rotation(reference).inv()).as_rotvec()
    residual = vector.copy(); residual[axis] = 0
    if np.linalg.norm(residual) > .03 or abs(vector[axis]) > limit + 1e-6:
        raise RequestRejected("Target exceeds the backend's permitted rotation axis/range")
    return float(vector[axis])


def latched_grip(opening):
    return 1.0 if opening <= .001 else -float(opening)
