"""Bounded world-frame pose corrections using NumPy only."""
import numpy as np


def vector(value, size, name):
    if (not isinstance(value, (list, tuple)) or len(value) != size
            or any(type(item) not in (int, float) for item in value) or not np.isfinite(value).all()):
        raise ValueError(f"{name} requires {size} finite numbers")
    return np.asarray(value, dtype=float)


def quaternion(value):
    q = vector(value, 4, "quaternion_wxyz")
    length = float(np.linalg.norm(q))
    if abs(length - 1) > 1e-4:
        raise ValueError("quaternion_wxyz must have unit length")
    return q / length


def multiply(left, right):
    """Hamilton product in wxyz order; left applies in the world frame."""
    a, b = np.asarray(left), np.asarray(right)
    return np.r_[a[0] * b[0] - np.dot(a[1:], b[1:]),
                 a[0] * b[1:] + b[0] * a[1:] + np.cross(a[1:], b[1:])]


def rotation_distance(left, right):
    a, b = quaternion(left), quaternion(right)
    relative = multiply(a, b * np.array([1., -1., -1., -1.]))
    # q and -q encode the same rotation. atan2 retains precision near zero.
    return float(2 * np.arctan2(np.linalg.norm(relative[1:]), abs(relative[0])))


def rotate_world(value, rotation_vector):
    q = quaternion(value)
    delta = vector(rotation_vector, 3, "rotation_vector")
    angle = float(np.linalg.norm(delta))
    scale = .5 * np.sinc(angle / (2 * np.pi))
    change = np.r_[np.cos(angle / 2), scale * delta]
    result = multiply(change, q)
    return (result / np.linalg.norm(result)).tolist()


def validate_target(target, measured, *, translation_limit=.05, rotation_limit=.35):
    offset = vector(target["position"], 3, "position") - measured["position"]
    angle = rotation_distance(target["quaternion_wxyz"], measured["quaternion"])
    if np.linalg.norm(offset) > translation_limit + 1e-10 or angle > rotation_limit + 1e-10:
        raise ValueError("Target exceeds the declared Cartesian correction bounds")


def edit_pose(original, delta_position, delta_rotation_vector, fraction):
    """One absolute edited control-point target, anchored to the original preview."""
    dp = vector(delta_position, 3, "delta_position")
    dr = vector(delta_rotation_vector, 3, "delta_rotation_vector")
    if np.linalg.norm(dp) > .05 + 1e-10 or np.linalg.norm(dr) > .35 + 1e-10:
        raise ValueError("Edit exceeds 0.05 metres or 0.35 radians")
    return {"position": (np.asarray(original["position"]) + fraction * dp).tolist(),
            "quaternion_wxyz": rotate_world(original["quaternion_wxyz"], (fraction * dr).tolist())}
