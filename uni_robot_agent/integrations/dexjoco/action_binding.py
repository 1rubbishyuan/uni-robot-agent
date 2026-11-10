"""Bind declared TCP and Allegro semantics to coordinated device steps."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image

from uni_robot_agent.services.actor.contracts import (
    NumericChunk,
    ActorField,
    ActorObservation,
    ActorProfile,
    validate_vector,
)
from uni_robot_agent.services.actor.images import encode_image
from uni_robot_agent.services.environment.contracts import Pose
from uni_robot_agent.services.environment.device_contracts import (
    CoordinatedDeviceStep,
    DeviceEpisodeInfo,
    DeviceRequest,
)
from uni_robot_agent.tools.hybrid.contracts import (
    HybridCapabilities,
    Proposal,
    StepOutcome,
)
from uni_robot_agent.tools.hybrid.decisions import validate_decision

IMAGE_CONVENTION = "RGB HWC; model preprocessing belongs to the upstream adapter"


def _pose_fields(group, arm, *, rotation_vector=False):
    common = {"frame": "world", "control_point": arm.control_frame}
    fields = [ActorField(name=f"{group}_position_{axis}", unit="m", semantics="position", **common)
              for axis in "xyz"]
    component, axes, unit, semantics = (("rotation_vector", "xyz", "rad", "rotation_vector_absolute")
        if rotation_vector else ("quaternion", "wxyz", "unitless", "quaternion_wxyz"))
    return fields + [ActorField(name=f"{group}_{component}_{axis}", unit=unit, semantics=semantics, **common)
                     for axis in axes]


def _hand_fields(hand):
    return [ActorField(name=f"{hand.name}_{joint}", unit="rad", semantics="joint_position",
                       control_point=hand.name) for joint in hand.joint_names]


def actor_profile(environment):
    """Describe physical vectors; model latent padding is not a robot dimension."""
    devices = {item.name: item for item in environment.devices}
    layout = environment.metadata["layout"]
    # The published dual-arm observation puts both TCP poses before both hands.
    observations = [field for item in layout
                    for field in _pose_fields(item["group"], devices[item["arm"]])]
    observations += [field for item in layout for field in _hand_fields(devices[item["hand"]])]
    actions = [field for item in layout
               for field in (*_pose_fields(item["group"], devices[item["arm"]], rotation_vector=True),
                             *_hand_fields(devices[item["hand"]]))]
    return ActorProfile(name="dexjoco_single_arm_tcp_hand" if len(layout) == 1 else "dexjoco_dual_arm_tcp_hand",
        cameras=["front", "wrist"] if len(layout) == 1 else ["front", "right_wrist", "left_wrist"],
        observation_fields=observations, action_fields=actions, action_horizon=30,
        control_frequency_hz=environment.metadata["control_frequency_hz"], image_convention=IMAGE_CONVENTION)


def _quaternion(vector):
    angle = math.sqrt(sum(float(value) ** 2 for value in vector))
    scale = .5 if angle < 1e-10 else math.sin(angle / 2) / angle
    return [math.cos(angle / 2), *(float(value) * scale for value in vector)]


def _rotation_vector(quaternion):
    values = np.asarray(quaternion, dtype=float)
    if values[0] < 0:
        values = -values
    sine = float(np.linalg.norm(values[1:]))
    scale = 2. if sine < 1e-10 else 2 * math.atan2(sine, float(values[0])) / sine
    return (values[1:] * scale).tolist()


class DexjocoBinding:
    name = "dexjoco_tcp_hand"
    reviewer_supported = True
    reviewer_prompt = Path(__file__).resolve().parents[2] / "resources/prompts/actor/hybrid_decision.md"

    def __init__(self, description, actor_info):
        if not isinstance(description, DeviceEpisodeInfo) or description.integration != "dexjoco":
            raise ValueError("DexJoCo binding requires the dexjoco device protocol")
        self.description, self.info = description, actor_info
        environment = description.environment
        self.devices = {item.name: item for item in environment.devices}
        self.layout = environment.metadata.get("layout", [])
        if environment.world_frame != "world" or len(self.layout) not in (1, 2):
            raise ValueError("DexJoCo requires one or two declared groups in the world frame")
        groups, members = [], []
        self.limits = {}
        for item in self.layout:
            if set(item) != {"group", "arm", "hand"} or not all(isinstance(value, str) and value for value in item.values()):
                raise ValueError("Each layout entry requires a group, arm and hand")
            arm, hand = self.devices[item["arm"]], self.devices[item["hand"]]
            if arm.kind != "arm" or arm.control_frame != "attachment_site" or hand.kind != "hand":
                raise ValueError("DexJoCo requires the measured attachment_site TCP and an Allegro hand")
            if hand.joint_names != [f"{finger}j{index}" for finger in ("ff", "mf", "rf", "th") for index in range(4)]:
                raise ValueError("Hand joints must follow the published Allegro order")
            limits = np.asarray(hand.metadata.get("actuator_target_limits", []), dtype=float)
            if (hand.metadata.get("target_limit_policy") != "clip" or limits.shape != (16, 2)
                    or not np.isfinite(limits).all() or np.any(limits[:, 0] > limits[:, 1])):
                raise ValueError("Hand actuator target clipping must have sixteen finite explicit ranges")
            self.limits[hand.name] = limits
            groups.append(item["group"])
            members.extend((arm.name, hand.name))
        if len(set(groups)) != len(groups) or len(set(members)) != len(members):
            raise ValueError("Control groups must reference distinct devices")
        assembly = self.devices.get("robot")
        if assembly is None or assembly.kind != "assembly" or "coordinated_step" not in assembly.capabilities:
            raise ValueError("DexJoCo requires a coordinated robot assembly")
        if set(self.devices) != {*members, "robot"}:
            raise ValueError("The binding must account for every declared robot device")
        self.profile = actor_info.profile
        if self.profile != actor_profile(environment):
            raise ValueError("Actor must declare the complete matching DexJoCo physical profile")
        if not set(self.profile.cameras) <= {camera.name for camera in environment.cameras}:
            raise ValueError("Required actor cameras are unavailable")
        self.capabilities = HybridCapabilities(groups=tuple(groups),
            constraint_schema={"type": "object", "properties": {}, "additionalProperties": False},
            default_actions=30, max_actions=30, default_max_chunks=6)

    @property
    def action_horizon(self):
        return self.profile.action_horizon

    def validate_scope(self, active_groups, constraints):
        if (not isinstance(active_groups, list) or not active_groups
                or not all(isinstance(group, str) for group in active_groups)
                or len(set(active_groups)) != len(active_groups)
                or not set(active_groups) <= set(self.capabilities.groups)):
            raise ValueError("active_groups must name distinct declared groups")
        if not isinstance(constraints, dict) or constraints:
            raise ValueError("DexJoCo currently supports only empty constraints")
        return {"active_groups": list(active_groups)}

    def _measured(self, manifest):
        state = manifest["state"]["devices"]
        poses, joints = {}, {}
        for item in self.layout:
            poses[item["arm"]] = Pose.model_validate(state[item["arm"]]["control_pose"])
            hand = self.devices[item["hand"]]
            measured = state[hand.name]["joint_positions_rad"]
            if set(measured) != set(hand.joint_names):
                raise ValueError("Measured hand joints differ from the declared device")
            values = [float(measured[name]) for name in hand.joint_names]
            if not all(math.isfinite(value) for value in values):
                raise ValueError("Measured finger joints must be finite")
            joints[hand.name] = dict(zip(hand.joint_names, values, strict=True))
        return poses, joints

    def encode_observation(self, inputs, instruction):
        manifest, images = inputs.manifest, inputs.images
        poses, joints = self._measured(manifest)
        values = [value for item in self.layout
                  for value in (*poses[item["arm"]].position, *poses[item["arm"]].quaternion)]
        values += [value for item in self.layout for value in joints[item["hand"]].values()]
        validate_vector(values, self.profile.observation_fields, "observation")
        encoded = {}
        for name in self.profile.cameras:
            with Image.open(images[name]) as image:
                encoded[name] = encode_image(np.asarray(image.convert("RGB")))
        return ActorObservation(instruction=instruction, state=values, images=encoded)

    def decode_prediction(self, prediction, manifest, images, scope):
        if not isinstance(prediction, NumericChunk):
            raise ValueError("This binding requires numeric actor predictions")
        actions = prediction.actions
        raw = np.asarray(actions, dtype=float)
        if raw.shape != (self.action_horizon, len(self.profile.action_fields)) or not np.isfinite(raw).all():
            raise ValueError("Actor returned an invalid DexJoCo action matrix")
        poses, joints = self._measured(manifest)
        scoped, rows, clipped = raw.copy(), [], 0
        for values in scoped:
            validate_vector(values.tolist(), self.profile.action_fields, "action")
            targets, hand_targets = {}, {}
            offset = 0
            for item in self.layout:
                arm, hand = item["arm"], item["hand"]
                if item["group"] not in scope["active_groups"]:
                    # Latch inactive groups to this observation, avoiding drifting hold targets.
                    values[offset:offset + 6] = [*poses[arm].position, *_rotation_vector(poses[arm].quaternion)]
                    values[offset + 6:offset + 22] = list(joints[hand].values())
                limits = self.limits[hand]
                requested = values[offset + 6:offset + 22].copy()
                applied = np.clip(requested, limits[:, 0], limits[:, 1])
                clipped += int(np.count_nonzero(requested != applied))
                values[offset + 6:offset + 22] = applied
                targets[arm] = Pose(position=values[offset:offset + 3].tolist(),
                                    quaternion=_quaternion(values[offset + 3:offset + 6]))
                hand_targets[hand] = dict(zip(self.devices[hand].joint_names, applied.tolist(), strict=True))
                offset += 22
            command = CoordinatedDeviceStep(device="robot", poses=targets, joints=hand_targets)
            self._validate_command(command)
            rows.append(command.model_dump(mode="json"))
        return Proposal(rows=rows, state_version=manifest["state"]["state_version"],
            current_state=manifest["state"]["devices"], images=images, raw_actions=raw, scoped_actions=scoped,
            metadata={"action_kind": "absolute_tcp_rotation_vector_and_finger_positions",
                "episode_started": manifest["state"]["completed_commands"] > 0,
                "finger_target_limit_policy": "clip_to_native_actuator_ranges", "clipped_values": clipped,
                "inactive_group_policy": "hold_captured_measured_pose_and_fingers"})

    def preview(self, proposal, environment):
        return {"trajectory": [{"poses": row["poses"], "joints": row["joints"]} for row in proposal.rows],
            "interpretation": "Requested world TCP and finger targets; not predicted motion, contact or collision clearance."}

    def review_context(self, proposal, scope):
        return {"current_state": proposal.current_state, "preview": proposal.preview, "scope": scope,
            "episode_started": proposal.metadata["episode_started"],
            "device_descriptions": [item.model_dump(mode="json") for item in self.devices.values()]}

    def validate_decision(self, decision, context, actions_per_chunk):
        return validate_decision(decision, context, actions_per_chunk)

    def plan(self, proposal, decision, scope):
        if decision["mode"] == "stop":
            return []
        if decision["mode"] != "student" or type(decision["steps"]) is not int or not 1 <= decision["steps"] <= len(proposal.rows):
            raise ValueError("DexJoCo only permits a bounded student prefix or stop")
        rows = proposal.rows[:decision["steps"]]
        for row in rows:
            self._validate_command(CoordinatedDeviceStep.model_validate(row))
        return rows

    def _validate_command(self, command):
        if (command.device != "robot" or set(command.poses) != {item["arm"] for item in self.layout}
                or set(command.joints) != {item["hand"] for item in self.layout}):
            raise ValueError("A coordinated sample must address all declared arm and hand devices")
        for name, values in command.joints.items():
            names = self.devices[name].joint_names
            if set(values) != set(names):
                raise ValueError("A coordinated sample must address every finger joint")
            ordered = np.asarray([values[joint] for joint in names])
            limits = self.limits[name]
            if np.any(ordered < limits[:, 0]) or np.any(ordered > limits[:, 1]):
                raise ValueError("Finger target is outside its declared actuator range")

    def compile_row(self, row, state):
        command = CoordinatedDeviceStep.model_validate(row)
        self._validate_command(command)
        return command

    def dispatch(self, environment, command, expected_state_version):
        return environment.device_step(DeviceRequest(command=command, expected_state_version=expected_state_version))

    def outcome(self, reply, command):
        feedback, status = reply.feedback, reply.execution_status
        count = feedback["steps_executed"]
        if type(count) is not int or count not in (0, 1) or (status == "completed" and count != 1):
            raise ValueError("A completed coordinated receipt must establish exactly one native sample")
        return StepOutcome(status=status, completed_samples=int(status == "completed"),
            execution_started=count > 0, duration_s=feedback["duration_s"],
            stop_reason="native_terminal" if reply.state_after.ended else feedback.get("stop_reason"),
            feedback=feedback)


def create_binding(name, args, description, actor_info):
    if name not in (None, DexjocoBinding.name):
        raise ValueError("Select dexjoco_tcp_hand for this actor profile")
    return DexjocoBinding(description, actor_info)
