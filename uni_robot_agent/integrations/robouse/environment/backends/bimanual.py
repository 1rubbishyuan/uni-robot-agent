"""Fixed-base Menagerie and composed Panda/ALOHA command translation."""
import numpy as np
from ..control import state, quaternion, fixed_rotation, axis_angle, latched_grip


class Bimanual:
    hz = 20.

    def __init__(self, backend, kind):
        self.b, self.kind = backend, kind
        if kind == "menagerie":
            self.model, self.data = backend.sim.model, backend.sim.data
            self.names = tuple(backend.driven)
            if backend.arms_mode == "switch":
                raise ValueError("Arm switching is not admitted")
            self.reference = {n: backend.scene.arms[n].down_quat for n in self.names}
            self.constraint = "Top-down orientation; world Z yaw only when yaw_enabled is true."
        else:
            self.model, self.data = backend.model, backend.data
            self.names = tuple(backend.driver.arms)
            self.reference = {n: backend.driver.cfg[n].down_quat for n in self.names}
            self.constraint = "Translation only; keep each arm's initial top-down orientation."

    def read(self, commands):
        b, result = self.b, {}
        for name in self.names:
            if self.kind == "menagerie":
                pos, quat = b.sim.tcp_pose(name)
                opening = np.mean(np.abs(b.sim.finger_positions(name))) / b.scene.arms[name].finger_open_m
            else:
                site = self.data.site(b.driver.cfg[name].tcp)
                pos, quat = site.xpos, quaternion(site.xmat)
                opening = b.driver.opening(name)
            result[name] = state(pos, quat, opening, commands[name])
        return result

    def encode(self, targets):
        b, result = self.b, []
        for name in self.names:
            target = targets[name]
            extra = []
            if self.kind == "menagerie":
                from robouse.backends.menagerie.backend import YAW_LIMIT, YAW_STEP, STEP_M
                if b.yaw_enabled:
                    angle = axis_angle(target.pose.quaternion, self.reference[name], 2, YAW_LIMIT)
                    extra = [(angle - b.yaw_cmd[name]) / YAW_STEP]
                else:
                    fixed_rotation(target.pose.quaternion, self.reference[name])
                origin, scale = b.target[name], STEP_M
            else:
                fixed_rotation(target.pose.quaternion, self.reference[name])
                origin, scale = b.driver.target[name], b.driver.STEP
            delta = (np.asarray(target.pose.position) - origin) / scale
            result.extend(np.clip(np.r_[delta, extra, latched_grip(target.gripper)], -1, 1))
        return list(map(float, result))
