"""Panda OSC backends with measured fingertip-centre reference points."""
import numpy as np
from ..control import state, quaternion, rotation
from uni_robot_agent.services.environment.contracts import ArmControlCapabilities, RequestRejected


class OSC:
    hz = 20.
    constraint = "Full world-frame pose control at the midpoint of the finger pads."

    def __init__(self, backend, kind):
        self.b, self.kind = backend, kind
        self.model, self.data = backend.mj_model_data()
        self.robots = backend.env.robots
        self.names = tuple("arm" if len(self.robots) == 1 else f"arm{i}" for i in range(len(self.robots)))
        # Only the reviewed LIBERO controller is admitted for actor control.
        self.actor_control = ArmControlCapabilities(commands=["pose"]) if kind == "libero" else None
        self.control_point = "panda_fingerpad_midpoint"
        self.entries = []
        offset = 0
        for robot in self.robots:
            if kind == "libero":
                gripper, controller, site = robot.gripper, robot.controller, robot.eef_site_id
                arm_slice, grip_index = slice(0, 6), 6
            else:
                if len(robot.arms) != 1:
                    raise ValueError("Only one Panda arm per robosuite robot is admitted")
                arm = robot.arms[0]
                gripper, controller, site = robot.gripper[arm], robot.part_controllers[arm], robot.eef_site_id[arm]
                start, end = robot._action_split_indexes[arm]
                gs, ge = robot._action_split_indexes[f"{arm}_gripper"]
                if end - start != 6 or ge - gs != 1:
                    raise ValueError("Expected a six-axis OSC arm and scalar gripper")
                arm_slice, grip_index = slice(offset + start, offset + end), offset + gs
            if type(gripper).__name__ != "PandaGripper" or controller.name != "OSC_POSE":
                raise ValueError("Only PandaGripper with OSC_POSE is admitted in this release")
            pads = [gripper.important_geoms[k] for k in ("left_fingerpad", "right_fingerpad")]
            ids = [[self.model.geom(n).id for n in group] for group in pads]
            if getattr(controller, "input_ref_frame", "world") != "world":
                raise ValueError("OSC controller must accept world-frame deltas")
            self.entries.append((controller, int(site), ids, arm_slice, grip_index, robot, gripper))
            offset += robot.action_dim

    def _pose(self, entry):
        _, site, pads, _, _, robot, _ = entry
        points = [self.data.geom_xpos[ids].mean(axis=0) for ids in pads]
        centre = np.mean(points, axis=0)
        quat = quaternion(self.data.site_xmat[site])
        indexes = robot._ref_gripper_joint_pos_indexes
        if isinstance(indexes, dict):
            indexes = indexes[robot.arms[0]]
        width = float(np.abs(self.data.qpos[indexes]).sum())
        return centre, quat, width

    def read(self, commands):
        result = {}
        for name, entry in zip(self.names, self.entries):
            centre, quat, width = self._pose(entry)
            result[name] = state(centre, quat, width / .08, commands[name])
        return result

    def encode(self, targets):
        action = np.zeros(self.b.action_spec.dim)
        for name, entry in zip(self.names, self.entries):
            controller, site, _, arm_slice, grip_index, _, gripper = entry
            target = targets[name]
            centre, quat, width = self._pose(entry)
            current_rotation, target_rotation = rotation(quat), rotation(target.pose.quaternion)
            local_offset = current_rotation.inv().apply(centre - self.data.site_xpos[site])
            native_target = np.asarray(target.pose.position) - target_rotation.apply(local_offset)
            delta = np.r_[native_target - self.data.site_xpos[site], (target_rotation * current_rotation.inv()).as_rotvec()]
            low, high = np.asarray(controller.output_min), np.asarray(controller.output_max)
            ilow, ihigh = np.asarray(controller.input_min), np.asarray(controller.input_max)
            if np.any(high <= low):
                raise RequestRejected("OSC controller has invalid scaling")
            action[arm_slice] = np.clip(ilow + (delta-low)*(ihigh-ilow)/(high-low), ilow, ihigh)
            opening_command = (1. + float(np.asarray(gripper.current_action).ravel()[0])) / 2
            error = opening_command - target.gripper
            action[grip_index] = 0. if abs(error) < float(gripper.speed) / 4 + 1e-8 else np.sign(error)
        return action.tolist()
