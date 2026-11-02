"""Robot-only conversion between jaw opening in meters and the native drive.

The two Robotiq pads translate nonlinearly as finger_joint rotates. Calibrate
from their USD geometry and ideal linkage; measure feedback from actual body
poses so passive-joint deformation is not hidden by the drive angle.
"""

import numpy as np


MAX_OPENING_M = 0.085
NATIVE_ANGLE_SCALE = np.pi / 4


class JawOpening:
    def __init__(self, robot, joint_limits_rad):
        self.flange_body = robot.ee_body
        self.pads = {}
        for side in ("left", "right"):
            matches = [g for g in robot.public["collision_geometry"]
                       if g["body"].endswith(f"/{side}_inner_finger")
                       and "fingertips" in g["source_prim"]]
            if len(matches) != 1:
                raise ValueError(f"Expected one {side} Robotiq fingertip pad")
            self.pads[side] = (matches[0]["body"], np.asarray(matches[0]["vertices"]))
        angles = np.linspace(*joint_limits_rad, 257)
        commands = angles / NATIVE_ANGLE_SCALE
        widths = np.array([self.measure(robot.transforms(np.zeros(7), g)) for g in commands])
        if not np.all(np.diff(widths) < 0):
            raise ValueError("Robotiq jaw opening must decrease monotonically across joint limits")
        if abs(widths[0] - MAX_OPENING_M) > 1e-4:
            raise ValueError("Robot geometry does not match the 85 mm Robotiq opening")
        self.widths = widths
        self.commands = commands
        self.public = {
            "units": "m", "command": "full gap between the two fingertip pads",
            "open": MAX_OPENING_M, "closed": 0.0,
            "reachable_opening_m": [float(widths[-1]), float(widths[0])],
            "joint_limits_rad": list(map(float, joint_limits_rad)),
            "limit_behavior": "Requested gaps outside the geometric range saturate at native joint limits",
            "measurement": "Signed gap between projected inward pad surfaces along flange +Y, from actual body poses",
            "flange_body": self.flange_body,
            "pad_bodies": {side: body for side, (body, _) in self.pads.items()},
            "opening_to_joint": [{"opening_m": float(w), "finger_joint_rad": float(a)}
                                 for w, a in zip(widths, angles)],
        }

    def native_command(self, opening_m):
        """Invert the geometric gap, saturating only at native travel limits."""
        widths = np.asarray(opening_m, dtype=float)
        if not np.isfinite(widths).all() or np.any((widths < 0) | (widths > MAX_OPENING_M)):
            raise ValueError("Gripper opening must be between 0 and 0.085 meters")
        return np.interp(widths, self.widths[::-1], self.commands[::-1])

    def measure(self, body_poses):
        """Read physical pad separation; requires only three robot body poses."""
        flange_inverse = np.linalg.inv(body_poses[self.flange_body])
        edges = {}
        for side, (body, vertices) in self.pads.items():
            transform = flange_inverse @ body_poses[body]
            y = vertices @ transform[1, :3] + transform[1, 3]
            edges[side] = y.min() if side == "left" else y.max()
        return float(edges["left"] - edges["right"])
