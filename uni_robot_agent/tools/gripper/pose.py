"""Pose math in metres/wxyz, with fixed-axis XYZ conversion at the tool boundary."""

from __future__ import annotations

import math

import numpy as np


def vector(value, length: int, name: str) -> np.ndarray:
    result = np.asarray(value, dtype=np.float64)
    if result.shape != (length,) or not np.isfinite(result).all():
        raise ValueError(f"{name} must be a finite {length}-vector")
    return result.copy()


def scalar(value, name: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result) or not low <= result <= high:
        raise ValueError(f"{name} must be in [{low}, {high}]")
    return result


def quaternion(value, name: str = "quaternion_wxyz") -> np.ndarray:
    result = vector(value, 4, name)
    length = float(np.linalg.norm(result))
    if length <= 1e-12:
        raise ValueError(f"{name} cannot be zero")
    return result / length


def quaternion_distance(left, right) -> float:
    dot = float(np.clip(abs(np.dot(quaternion(left), quaternion(right))), 0, 1))
    return 2 * math.acos(dot)


def quaternion_multiply(left, right) -> np.ndarray:
    lw, lx, ly, lz = left
    rw, rx, ry, rz = right
    return np.array([
        lw * rw - lx * rx - ly * ry - lz * rz,
        lw * rx + lx * rw + ly * rz - lz * ry,
        lw * ry - lx * rz + ly * rw + lz * rx,
        lw * rz + lx * ry - ly * rx + lz * rw,
    ])


def rotate_vector(orientation, offset) -> np.ndarray:
    q = quaternion(orientation)
    point = vector(offset, 3, "offset")
    return point + 2 * np.cross(q[1:], np.cross(q[1:], point) + q[0] * point)


def orientation_waypoint(current, target, max_angle: float = 0.6) -> np.ndarray:
    start, goal = quaternion(current), quaternion(target)
    dot = float(np.dot(start, goal))
    if dot < 0:
        goal, dot = -goal, -dot
    half_angle = math.acos(float(np.clip(dot, 0, 1)))
    if half_angle < 1e-12:
        return goal
    fraction = min(1.0, max_angle / (2 * half_angle))
    return quaternion(
        (math.sin((1 - fraction) * half_angle) * start
         + math.sin(fraction * half_angle) * goal) / math.sin(half_angle)
    )


def pose_waypoint(
    current, target_xyz, target_quat, *, max_step_m: float,
    rotation_radius_m: float,
    max_rotation_rad: float = 0.6,
) -> np.ndarray:
    """Bound site translation plus rotational displacement within a declared radius."""
    radius = scalar(rotation_radius_m, "rotation_radius_m", 0, math.inf)
    angle_limit = max_rotation_rad
    if radius > 1e-12:
        angle_limit = min(angle_limit, 2 * math.asin(min(1.0, max_step_m / (4 * radius))))
    next_quat = orientation_waypoint(current[3:], target_quat, angle_limit)
    # Rotation uses at most half the displacement budget, leaving room for translation.
    rotation_motion = 2 * radius * math.sin(quaternion_distance(next_quat, current[3:]) / 2)
    translation_budget = max(0.0, max_step_m - rotation_motion)
    origin = np.asarray(current[:3], dtype=float)
    delta = np.asarray(target_xyz, dtype=float) - origin
    fraction = min(1.0, translation_budget / max(float(np.linalg.norm(delta)), 1e-12))
    next_xyz = origin + fraction * delta
    return np.concatenate((next_xyz, next_quat))


def relative_rotation(current, axis, angle_rad: float, frame: str) -> np.ndarray:
    direction = vector(axis, 3, "axis")
    length = float(np.linalg.norm(direction))
    if length <= 1e-12:
        raise ValueError("axis cannot be zero")
    rotation = np.r_[math.cos(angle_rad / 2), direction / length * math.sin(angle_rad / 2)]
    orientation = quaternion(current)
    if frame == "world":
        return quaternion(quaternion_multiply(rotation, orientation))
    if frame == "local":
        return quaternion(quaternion_multiply(orientation, rotation))
    raise ValueError("frame must be world or local")


def rpy_to_quaternion(value) -> np.ndarray:
    """Fixed-axis XYZ radians: R = Rz(yaw) @ Ry(pitch) @ Rx(roll); return wxyz."""
    roll, pitch, yaw = vector(value, 3, "rpy") / 2
    cr, cp, cy = math.cos(roll), math.cos(pitch), math.cos(yaw)
    sr, sp, sy = math.sin(roll), math.sin(pitch), math.sin(yaw)
    return quaternion([
        cr * cp * cy + sr * sp * sy,
        sr * cp * cy - cr * sp * sy,
        cr * sp * cy + sr * cp * sy,
        cr * cp * sy - sr * sp * cy,
    ])


def quaternion_to_rpy(value) -> np.ndarray:
    """Return equivalent fixed-axis XYZ radians; use yaw=0 at gimbal lock."""
    w, x, y, z = quaternion(value)
    r00, r10, r20 = 1 - 2 * (y*y + z*z), 2 * (x*y + w*z), 2 * (x*z - w*y)
    r11, r12 = 1 - 2 * (x*x + z*z), 2 * (y*z - w*x)
    r21, r22 = 2 * (y*z + w*x), 1 - 2 * (x*x + y*y)
    horizontal = math.hypot(r00, r10)
    if horizontal < 1e-12:
        roll, yaw = math.atan2(-r12, r11), 0.
    else:
        roll, yaw = math.atan2(r21, r22), math.atan2(r10, r00)
    return np.array([roll, math.atan2(-r20, horizontal), yaw])
