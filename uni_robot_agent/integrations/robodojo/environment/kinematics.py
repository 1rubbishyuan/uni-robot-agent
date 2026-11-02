"""X5 registered grasp-site FK and native IK conversion; world metres and wxyz."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

# FK and bounded edits adapted from GPT-as-Policy 8f3d362;
# see SOURCE.md and UPSTREAM_LICENSE.


GRASP_SITE_URDF = Path(__file__).with_name("x5_grasp_site.urdf")
GRASP_SITE_DESCRIPTION = (
    "Nominal center between the two distal inner finger-pad area centroids; "
    "fixed to link6, not the fingertip or an object contact estimate. "
    "Local +X points toward the fingertips, fingers close along +/-Y, +Z completes "
    "the right-handed frame. Positions are metres and quaternions are wxyz. "
    "Calibrated tool openings use metres; native gripper values remain normalized 0 closed, 1 open. Neither is a contact sensor."
)


def grasp_site_definition():
    root = ET.parse(GRASP_SITE_URDF).getroot()
    joint = root.find("joint")
    origin = joint.find("origin")
    t = np.eye(4)
    t[:3, 3] = np.fromstring(origin.get("xyz"), sep=" ")
    t[:3, :3] = Rotation.from_euler("xyz", np.fromstring(origin.get("rpy"), sep=" ")).as_matrix()
    return joint.find("parent").get("link"), joint.find("child").get("link"), t


NATIVE_FRAME, GRASP_SITE, T_NATIVE_FROM_GRASP = grasp_site_definition()


def grasp_pose(native_pose):
    return pose(transform(native_pose[:3], native_pose[3:]) @ T_NATIVE_FROM_GRASP)


def native_pose(position, quaternion):
    value = pose(transform(position, quaternion) @ np.linalg.inv(T_NATIVE_FROM_GRASP))
    return np.r_[value["position"], value["quaternion_wxyz"]]


def physical_opening(robot, limits, joint):
    low, high = limits
    fraction = (float(joint) - low) / (high - low)
    return float(
        np.clip(fraction if robot.gripper_move["sign"] == 1 else 1 - fraction, 0, 1)
    )


def native_opening(robot, limits, fraction):
    # Observation normalization must not remove the native closing command's preload range.
    return float(np.clip(fraction, 0, 1))


def transform(position, quaternion_wxyz):
    q = np.asarray(quaternion_wxyz, float)
    t = np.eye(4)
    t[:3, :3] = Rotation.from_quat(q[[1, 2, 3, 0]]).as_matrix()
    t[:3, 3] = position
    return t


def pose(t):
    q = Rotation.from_matrix(t[:3, :3]).as_quat()
    return {"position": t[:3, 3].tolist(), "quaternion_wxyz": q[[3, 0, 1, 2]].tolist()}



# Robot-only URDF FK and bounded local DLS; no planning or scene truth.
class ArmFK:
    def __init__(self, urdf, joint_names, base="base_link", tip="link6", *, extension=None):
        self.names = list(joint_names)
        root = ET.parse(urdf).getroot()
        if extension is not None:
            extra = ET.parse(extension).getroot()
            existing = {element.get("name") for element in root}
            if any(element.get("name") in existing for element in extra):
                raise ValueError("Grasp-site extension conflicts with native URDF")
            root.extend(extra)
        edges = {
            j.find("child").get("link"): j
            for j in root.findall("joint")
        }
        self.chain = []
        visited = set()
        while tip != base:
            if tip in visited or tip not in edges:
                raise ValueError("URDF has no valid root-to-tip chain")
            visited.add(tip)
            j = edges[tip]
            origin = j.find("origin")
            xyz = (
                np.fromstring(origin.get("xyz", "0 0 0"), sep=" ")
                if origin is not None
                else np.zeros(3)
            )
            rpy = (
                np.fromstring(origin.get("rpy", "0 0 0"), sep=" ")
                if origin is not None
                else np.zeros(3)
            )
            t = np.eye(4)
            t[:3, :3], t[:3, 3] = Rotation.from_euler("xyz", rpy).as_matrix(), xyz
            axis = j.find("axis")
            axis = (
                np.fromstring(axis.get("xyz"), sep=" ")
                if axis is not None
                else np.array([1.0, 0, 0])
            )
            self.chain.insert(0, (j.get("name"), j.get("type"), t, axis))
            tip = j.find("parent").get("link")
        moving = [n for n, kind, _, _ in self.chain if kind != "fixed"]
        if set(moving) != set(self.names):
            raise ValueError(f"URDF chain/arm joint mismatch: {moving}, {self.names}")

    def matrix(self, q):
        q = np.asarray(q, float)
        if q.shape != (len(self.names),) or not np.isfinite(q).all():
            raise ValueError("Invalid arm joint vector")
        values, t = dict(zip(self.names, q)), np.eye(4)
        for name, kind, origin, axis in self.chain:
            motion = np.eye(4)
            if kind in ("revolute", "continuous"):
                motion[:3, :3] = Rotation.from_rotvec(axis * values[name]).as_matrix()
            elif kind != "fixed":
                raise ValueError("Only fixed/revolute arm chain supported")
            t = t @ origin @ motion
        return t

    def bounded_target(self, q, limits, root, target):
        q = np.asarray(q, float)
        now = root @ self.matrix(q)
        goal = transform(target["position"], target["quaternion_wxyz"])
        translation = goal[:3, 3] - now[:3, 3]
        rotation = Rotation.from_matrix(goal[:3, :3] @ now[:3, :3].T).as_rotvec()
        def bounded(x, cap):
            return x * min(1.0, cap / max(np.linalg.norm(x), 1e-12))
        error = np.r_[bounded(translation, 0.02), bounded(rotation, 0.1)]
        jacobian = np.zeros((6, len(q)))
        for i in range(len(q)):
            shifted = q.copy()
            shifted[i] += 1e-5
            nxt = root @ self.matrix(shifted)
            jacobian[:3, i] = (nxt[:3, 3] - now[:3, 3]) / 1e-5
            jacobian[3:, i] = (
                Rotation.from_matrix(nxt[:3, :3] @ now[:3, :3].T).as_rotvec() / 1e-5
            )
        delta = jacobian.T @ np.linalg.solve(
            jacobian @ jacobian.T + 0.05**2 * np.eye(6), error
        )
        limits = np.asarray(limits, float)
        low, high = (
            np.maximum(limits[:, 0], q - 0.05),
            np.minimum(limits[:, 1], q + 0.05),
        )
        if np.any(low > high):
            raise ValueError("Joint outside bounded valid interval")
        result = np.clip(q + delta, low, high).astype(np.float32)
        return result, {
            "target_position_error_m": float(np.linalg.norm(translation)),
            "target_rotation_error_rad": float(np.linalg.norm(rotation)),
            "proposed_joint_delta": (result - q).tolist(),
            "bounded_task_delta": error.tolist(),
            "method": "robot_only_numerical_jacobian_dls",
            "physical_tracking_verified": False,
        }


class DualKinematics:
    def __init__(self, env, *, slot=0):
        self.env, self.manager, self.slot = env, env.robot_manager, slot
        self.robots = {
            r.arm_name.split("_")[0]: r
            for r in self.manager.robot_list
            if r.type == "target"
        }
        if set(self.robots) != {"left", "right"}:
            raise ValueError("Expected left/right target X5 arms")
        if any(r.ee_link_name != NATIVE_FRAME for r in self.robots.values()):
            raise ValueError("X5 grasp-site calibration requires the native link6 controller")
        self.native_fk = {
            arm: ArmFK(r.urdf_path, r.arm_joints_name, r.base_link, r.ee_link_name)
            for arm, r in self.robots.items()
        }

        self.fk = {
            arm: ArmFK(r.urdf_path, r.arm_joints_name, r.base_link, GRASP_SITE, extension=GRASP_SITE_URDF)
            for arm, r in self.robots.items()
        }

    def root(self, arm):
        robot = self.robots[arm]
        value = self.manager.get_link_pose(robot, robot.base_link, is_relative=True)[self.slot]
        return transform(value[:3], value[3:])

    def check(self):
        checks = {}
        for arm, r in self.robots.items():
            q = self.manager.get_joint(r)[self.slot]
            predicted = self.root(arm) @ self.native_fk[arm].matrix(q)
            measured = self.manager.get_real_endpose(r, is_relative=True)[self.slot]
            error = np.linalg.norm(predicted[:3, 3] - measured[:3])
            angle = Rotation.from_matrix(
                predicted[:3, :3] @ transform(measured[:3], measured[3:])[:3, :3].T
            ).magnitude()
            checks[arm] = {
                "position_error_m": float(error),
                "rotation_error_rad": float(angle),
                "passed": bool(error < 0.002 and angle < 0.01),
            }
        if not all(c["passed"] for c in checks.values()):
            raise ValueError(f"Robot FK does not match measured link6: {checks}")
        return {"arms": checks, "passed": True, "physical_steps": 0}

    def preview(self, actions):
        a = np.asarray(actions)
        if a.ndim != 2 or a.shape[1] != 14 or not 1 <= len(a) <= 50 or not np.isfinite(a).all():
            raise ValueError("Expected 1..50 dual X5 joint targets of width 14")
        checks = self.check()
        roots = {arm: self.root(arm) for arm in ("left", "right")}
        trajectory = [
            dict(
                index=i,
                **{
                    arm: dict(
                        **pose(
                            roots[arm] @ self.fk[arm].matrix(row[offset : offset + 6])
                        ),
                        gripper_closed=bool(row[offset + 6] < 0.5),
                        gripper_opening=float(row[offset + 6]),
                    )
                    for arm, offset in (("left", 0), ("right", 7))
                },
            )
            for i, row in enumerate(a)
        ]
        return {
            "trajectory": trajectory,
            "measured_fk_check": checks,
            "physical_steps": 0,
            "frame": "environment_origin",
            "control_frames": {arm: GRASP_SITE for arm in self.robots},
            "interpretation": "kinematic_targets_not_object_future",
        }

    def target(self, targets, *, joint_holds=None):
        joint_holds = joint_holds or {}
        if set(targets) & set(joint_holds) or set(targets) | set(joint_holds) != {"left", "right"}:
            raise ValueError("Exactly one pose or joint hold is required per arm")
        self.check()
        action, diagnostics = [], {}
        for arm in ("left", "right"):
            if arm in joint_holds:
                hold = joint_holds[arm]
                action.extend([*hold["joints"], hold["gripper"]])
                diagnostics[arm] = {"held_joints": True}
                continue
            robot, target = self.robots[arm], targets[arm]
            p, q = (
                np.asarray(target["position"], float),
                np.asarray(target["quaternion_wxyz"], float),
            )
            if (
                p.shape != (3,)
                or q.shape != (4,)
                or not np.isfinite(np.r_[p, q]).all()
                or abs(np.linalg.norm(q) - 1) > 1e-4
            ):
                raise ValueError("Finite pose and unit wxyz required")
            # Per-decision 5 cm / .35 rad bound is checked by the shared host;
            # each real ACK recomputes this bounded robot-only IK update.
            key = self.manager.robot_key[self.manager.robot_list.index(robot)]
            limits = (
                key.data.soft_joint_pos_limits[self.slot, robot.arm_joint_indices].cpu().numpy()
            )
            joints, diagnostics[arm] = self.fk[arm].bounded_target(
                self.manager.get_joint(robot)[self.slot], limits, self.root(arm), target
            )
            if type(target["gripper_closed"]) is not bool:
                raise ValueError("Explicit boolean gripper required")
            opening = target.get("gripper_opening", float(not target["gripper_closed"]))
            if not np.isfinite(opening) or not 0 <= opening <= 1:
                raise ValueError("Invalid gripper opening")
            action.extend([*joints.tolist(), opening])
        return {"action": action, "diagnostics": diagnostics, "physical_steps": 0}
