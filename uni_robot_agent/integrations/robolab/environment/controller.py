"""Bound one native DROID joint-position tick; shared tools own motion loops."""
import math
import numpy as np

from .gripper import MAX_OPENING_M, NATIVE_ANGLE_SCALE
from .kinematics import MotionRejected, feedback, pose


class Controller:
    slew_rad_s = 1.0
    planned_clearance_m = .002

    def __init__(self, robot, dt):
        self.robot, self.dt = robot, float(dt)
        if not np.isfinite(self.dt) or self.dt <= 0:
            raise ValueError("Native control timestep must be positive and finite")
        self.previous_q = None
        self.previous_drive = None

    def plan_pose(self, target_world, opening, measured):
        root = pose(measured["root_position"], measured["root_quaternion"])
        actual = np.linalg.inv(root) @ pose(measured["position"], measured["quaternion"])
        check = feedback(self.robot.fk(np.asarray(measured["joints"])), actual)
        if not check["target_reached"]:
            raise RuntimeError("Private DROID FK disagrees with measured flange pose")
        target = np.linalg.inv(root) @ target_world
        q = self.robot.ik(target, np.asarray(measured["joints"]))
        return self.plan_joints(q, opening, measured)

    def plan_joints(self, target, opening, measured):
        row = self.bounded_target(target, opening, measured)
        q = np.asarray(measured["joints"], dtype=float)
        start = q if self.previous_q is None else self.previous_q
        drive = measured["finger_joint_rad"] / NATIVE_ANGLE_SCALE
        drive_start = drive if self.previous_drive is None else self.previous_drive
        next_q, next_drive = row[:7], float(row[7])
        initial = self.robot.clearance(q, drive)
        previous = initial
        # Include measured-to-command tracking lag and bound swept robot geometry.
        for qa, qb, ga, gb in [(q, start, drive, drive_start), (start, next_q, drive_start, next_drive)]:
            count = max(1, math.ceil(self.robot.sweep_bound(qa, qb, ga, gb) / .001))
            for t in np.linspace(0, 1, count + 1)[1:]:
                distance = self.robot.clearance(qa + t * (qb - qa), ga + t * (gb - ga))
                if distance < self.planned_clearance_m and (
                        initial >= self.planned_clearance_m or distance < previous - 1e-6):
                    raise MotionRejected("Robot self-clearance preflight failed", clearance_m=distance)
                previous = distance
        return row

    def bounded_target(self, target, opening, measured):
        """Compute one drive target without stepping, collision queries or mutation."""
        target = np.asarray(target, dtype=float)
        if target.shape != (7,) or not np.isfinite(target).all():
            raise MotionRejected("DROID requires seven finite joint targets")
        if np.any(target < self.robot.lower) or np.any(target > self.robot.upper):
            raise MotionRejected("Arm joint target exceeds DROID limits")
        q = np.asarray(measured["joints"], dtype=float)
        start = q if self.previous_q is None else self.previous_q
        drive = measured["finger_joint_rad"] / NATIVE_ANGLE_SCALE
        drive_start = drive if self.previous_drive is None else self.previous_drive
        drive_target = float(self.robot.gripper.native_command(opening * MAX_OPENING_M))
        delta = np.r_[target - start, (drive_target - drive_start) * NATIVE_ANGLE_SCALE]
        fraction = min(1., self.slew_rad_s * self.dt / max(float(np.abs(delta).max()), 1e-12))
        next_q = start + (target - start) * fraction
        next_drive = drive_start + (drive_target - drive_start) * fraction
        return np.r_[next_q, next_drive]

    def snapshot(self):
        """Independent controller intent for nominal previews; robot geometry is read-only."""
        result = Controller(self.robot, self.dt)
        result.previous_q = None if self.previous_q is None else self.previous_q.copy()
        result.previous_drive = self.previous_drive
        return result

    def acknowledge(self, row):
        # Commit drive intent only after one native step was acknowledged.
        self.previous_q = np.asarray(row[:7]).copy()
        self.previous_drive = float(row[7])
