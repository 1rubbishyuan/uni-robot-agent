"""Pinned Tabletop, MetaWorld, Fetch and Kitchen native command semantics."""
import numpy as np
from ..control import state, quaternion, fixed_rotation, axis_angle, rotation


class Cartesian:
    names = ("arm",)

    def __init__(self, backend, kind):
        self.b, self.kind = backend, kind
        if kind == "gymrobotics":
            self.model, self.data = backend.u.model, backend.u.data
        else:
            self.model, self.data = backend.mj_model_data()
        self.reference = self.read({"arm": 1.0})["arm"].control_pose.quaternion
        self.hz = 12.5 if kind == "kitchen" else 20.
        if kind == "metaworld":
            self.hz = 1. / backend.env.dt
        elif kind == "gymrobotics":
            self.hz = 1. / backend.u.dt
        if kind == "tabletop":
            from robouse.backends.tabletop.scene import SUBSTEPS
            self.hz = 1. / (self.model.opt.timestep * SUBSTEPS)
        self.constraint = "Translation only; keep the initial orientation."
        if kind == "gymrobotics" and backend.u.block_gripper:
            self.constraint += " Gripper is locked closed: aperture must remain 0."
        if kind == "kitchen":
            self.reference = quaternion(backend._R0)
            self.constraint = "Translation and world +Y roll only, within +/-1.6 rad of the initial orientation."

    def read(self, commands):
        b, d = self.b, self.data
        if self.kind == "tabletop":
            pos, mat = d.site("tcp").xpos, d.site("tcp").xmat
            opening = sum(d.joint(n).qpos[0] for n in ("fl", "fr")) / .1
        elif self.kind == "metaworld":
            pos = b.hand_pos(); mat = d.body("hand").xmat
            opening = float(b._obs[3])
        elif self.kind == "gymrobotics":
            pos, mat = d.site("robot0:grip").xpos, d.site("robot0:grip").xmat
            opening = sum(d.joint(n).qpos[0] for n in ("robot0:l_gripper_finger_joint", "robot0:r_gripper_finger_joint")) / .1
        else:
            pos, mat = d.site_xpos[b._ee], d.site_xmat[b._ee]
            opening = d.qpos[7:9].sum() / .08
        return {"arm": state(pos, quaternion(mat), opening, commands["arm"])}

    def encode(self, targets):
        target = targets["arm"]
        b = self.b
        extra = []
        stationary = False
        if self.kind in ("kitchen", "metaworld"):
            # Grip/hold uses measured poses; physical orientation drift is not a rotation request.
            measured = self.read({"arm": target.gripper})["arm"].control_pose
            stationary = (np.allclose(target.pose.position, measured.position, atol=1e-7, rtol=0)
                          and (rotation(target.pose.quaternion) * rotation(measured.quaternion).inv()).magnitude() < 1e-7)
        if self.kind == "kitchen":
            angle = b._roll if stationary else axis_angle(target.pose.quaternion, self.reference, 1, 1.6)
            extra = [(angle - b._roll) / .1]
            origin, scale = b._target, .03
        else:
            if not stationary:
                fixed_rotation(target.pose.quaternion, self.reference)
            if self.kind == "tabletop":
                from robouse.backends.tabletop.scene import STEP_M
                origin, scale = self.data.mocap_pos[0], STEP_M
            elif self.kind == "metaworld":
                offset = b.hand_pos() - self.data.body("hand").xpos
                origin, scale = self.data.mocap_pos[0] + offset, b.env.action_scale
            else:
                origin, scale = self.data.site("robot0:grip").xpos, .05
        delta = (np.asarray(target.pose.position) - origin) / scale
        grip = 1 - 2 * target.gripper
        if self.kind == "metaworld":
            # Native control is finger effort, so regulate measured aperture.
            grip = np.clip(5 * (float(b._obs[3]) - target.gripper), -1, 1)
        elif self.kind == "gymrobotics":
            # Fetch position actuators add action to each measured finger joint.
            fingers = [self.data.joint(n).qpos[0] for n in
                       ("robot0:l_gripper_finger_joint", "robot0:r_gripper_finger_joint")]
            grip = float(np.mean(fingers)) - .05 * target.gripper
        if self.kind == "gymrobotics" and b.u.block_gripper and target.gripper > .001:
            from uni_robot_agent.services.environment.contracts import RequestRejected
            raise RequestRejected("This Fetch task locks the gripper closed")
        return np.clip(np.r_[delta, extra, grip], -1, 1).tolist()
