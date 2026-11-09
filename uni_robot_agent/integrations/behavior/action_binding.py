"""Bind R1Pro model semantics to atomic physical device commands."""
from pathlib import Path

import numpy as np
from PIL import Image

from uni_robot_agent.services.actor.contracts import ActorField, ActorObservation, ActorProfile, NumericChunk
from uni_robot_agent.services.actor.images import encode_image
from uni_robot_agent.services.environment.device_contracts import (
    BodyVelocityTarget, CoordinatedControlStep, DeviceEpisodeInfo, DeviceRequest, JointPositionTarget,
)
from uni_robot_agent.tools.hybrid.contracts import HybridCapabilities, Proposal, StepOutcome
from uni_robot_agent.tools.hybrid.decisions import validate_decision

CAMERAS = ["head", "left_wrist", "right_wrist"]
CONTROL_DT = 1 / 30
COMPONENTS = {"trunk": (3, 7), "left_arm": (7, 14), "left_gripper": (14, 15),
              "right_arm": (15, 22), "right_gripper": (22, 23)}
IMAGE_CONVENTION = "RGB HWC; model preprocessing belongs to the upstream adapter"


def actor_profile():
    def fields(prefix, count, unit, semantics, frame=None):
        return [ActorField(name=f"{prefix}_{index}", unit=unit, semantics=semantics, frame=frame)
                for index in range(count)]
    observations = [ActorField(name=f"base_{axis}", unit=unit, semantics="velocity", frame="base")
                    for axis, unit in (("vx", "m/s"), ("vy", "m/s"), ("wz", "rad/s"))]
    for side in ("left", "right"):
        observations += fields(f"{side}_arm_position", 7, "rad", "joint_position")
        observations += fields(f"{side}_arm_velocity", 7, "rad/s", "joint_velocity")
        observations += fields(f"{side}_eef_position", 3, "m", "position", "base")
        observations += fields(f"{side}_eef_quaternion_xyzw", 4, "unitless", "quaternion_xyzw", "base")
        observations += fields(f"{side}_finger_position", 2, "m", "joint_position")
        observations += fields(f"{side}_finger_velocity", 2, "m/s", "joint_velocity")
    observations += fields("trunk_position", 4, "rad", "joint_position")
    observations += fields("trunk_velocity", 4, "rad/s", "joint_velocity")
    actions = fields("base_command", 3, "normalized", "normalized_base_velocity", "base")
    actions += fields("trunk_position", 4, "rad", "joint_position")
    for side in ("left", "right"):
        actions += fields(f"{side}_arm_position", 7, "rad", "joint_position")
        actions += fields(f"{side}_gripper_command", 1, "normalized", "normalized_gripper_position")
    return ActorProfile(name="behavior_r1pro_joint", cameras=CAMERAS, observation_fields=observations,
        action_fields=actions, action_horizon=32, control_frequency_hz=30, image_convention=IMAGE_CONVENTION)


def finger_target(value, metadata):
    lower, upper = (float(item[0]) for item in metadata["input_limits"])
    command = np.clip(value, lower, upper)
    if metadata["inverted"]:
        command = upper - (command - lower)
    lo, hi = (float(item[0]) for item in metadata["output_limits"])
    return lo + (command - lower) * (hi - lo) / (upper - lower)


class BehaviorBinding:
    name = "behavior_r1pro_joint"
    reviewer_supported = True
    reviewer_prompt = Path(__file__).resolve().parents[2] / "resources/prompts/actor/hybrid_decision.md"

    def __init__(self, description, actor_info):
        if not isinstance(description, DeviceEpisodeInfo) or description.integration != "behavior":
            raise ValueError("BEHAVIOR binding requires a prepared BEHAVIOR device episode")
        self.description, self.profile = description, actor_info.profile
        if self.profile != actor_profile():
            raise ValueError("Actor must declare the complete R1Pro joint profile")
        self.devices = {item.name: item for item in description.environment.devices}
        if set(self.devices) != {"robot", "base", *COMPONENTS}:
            raise ValueError("R1Pro requires base, trunk, two arms, two grippers and their assembly")
        if (self.devices["robot"].kind != "assembly" or "coordinated_control" not in self.devices["robot"].capabilities
                or self.devices["base"].kind != "base"):
            raise ValueError("R1Pro requires atomic whole-body control")
        for name, (start, stop) in COMPONENTS.items():
            device = self.devices[name]
            unit = "m" if name.endswith("gripper") else "rad"
            size = 2 if unit == "m" else stop - start
            limits = np.asarray(device.joint_limits)
            if (len(device.joint_names) != size or device.joint_units != dict.fromkeys(device.joint_names, unit)
                    or limits.shape != (size, 2) or not np.isfinite(limits).all()):
                raise ValueError("R1Pro joint names, units and finite limits must match its profile")
            if unit == "m":
                metadata = device.metadata
                if (metadata.get("input_limits") != [[-1.], [1.]]
                        or not isinstance(metadata.get("inverted"), bool)
                        or np.shape(metadata.get("output_limits")) != (2, 1)):
                    raise ValueError("R1Pro grippers require their resolved native conversion")
        if not set(CAMERAS) <= {item.name for item in description.environment.cameras}:
            raise ValueError("R1Pro actor cameras are unavailable")
        self.capabilities = HybridCapabilities(groups=("whole_body",),
            constraint_schema={"type": "object", "properties": {}, "additionalProperties": False},
            default_actions=16, max_actions=32, default_max_chunks=6)

    @property
    def action_horizon(self):
        return self.profile.action_horizon

    def validate_scope(self, active_groups, constraints):
        if active_groups != ["whole_body"] or not isinstance(constraints, dict) or constraints:
            raise ValueError("R1Pro requires active_groups=['whole_body'] and empty constraints")
        return {"active_groups": ["whole_body"]}

    def encode_observation(self, inputs, instruction):
        state = inputs.manifest["state"]["devices"]["robot"]["proprioception"]
        values = np.asarray(state, dtype=float)
        if values.shape != (61,) or not np.isfinite(values).all():
            raise ValueError("Expected finite native R1Pro proprioception")
        images = {}
        for name in CAMERAS:
            with Image.open(inputs.images[name]) as image:
                images[name] = encode_image(np.asarray(image.convert("RGB")))
        return ActorObservation(instruction=instruction, state=values.tolist(), images=images)

    def decode_prediction(self, prediction, manifest, images, scope):
        if not isinstance(prediction, NumericChunk):
            raise ValueError("R1Pro requires a numeric actor chunk")
        raw = np.asarray(prediction.actions, dtype=float)
        if raw.shape != (32, 23) or not np.isfinite(raw).all():
            raise ValueError("Expected finite 32x23 R1Pro actions")
        rows, scoped, clipped = [], raw.copy(), 0
        for values in scoped:
            requested = values[:3].copy()
            values[:3] = np.clip(requested, -1, 1)
            clipped += int(np.count_nonzero(values[:3] != requested))
            joints = {}
            for name, (start, stop) in COMPONENTS.items():
                device = self.devices[name]
                limits = np.asarray(device.joint_limits)
                if name.endswith("gripper"):
                    target = finger_target(values[start], device.metadata)
                    applied = np.clip(np.full(2, target), limits[:, 0], limits[:, 1])
                    clipped += int(not -1 <= values[start] <= 1)
                    values[start] = np.clip(values[start], -1, 1)
                else:
                    applied = np.clip(values[start:stop], limits[:, 0], limits[:, 1])
                    clipped += int(np.count_nonzero(applied != values[start:stop]))
                    values[start:stop] = applied
                joints[name] = {joint: JointPositionTarget(position=float(value), unit=device.joint_units[joint])
                                for joint, value in zip(device.joint_names, applied, strict=True)}
            velocity = BodyVelocityTarget(frame="base", linear_m_s=[values[0] * .75, values[1] * .75, 0.],
                                          angular_rad_s=[0., 0., values[2]])
            command = CoordinatedControlStep(device="robot", duration_s=CONTROL_DT, joints=joints,
                                             velocities={"base": velocity})
            rows.append(command.model_dump(mode="json"))
        return Proposal(rows=rows, state_version=manifest["state"]["state_version"],
            current_state=manifest["state"]["devices"], images=images, raw_actions=raw, scoped_actions=scoped,
            metadata={"episode_started": manifest["state"]["completed_commands"] > 0,
                      "clipped_values": clipped, "action_kind": "absolute_joints_and_local_base_velocity"})

    def preview(self, proposal, environment):
        return {"trajectory": proposal.rows, "interpretation":
                "Requested local base velocities and absolute joint targets; not predicted contact, clearance or task success."}

    def review_context(self, proposal, scope):
        return dict(current_state=proposal.current_state, preview=proposal.preview, scope=scope,
                    episode_started=proposal.metadata["episode_started"],
                    device_descriptions=[item.model_dump(mode="json") for item in self.devices.values()])

    def validate_decision(self, decision, context, actions_per_chunk):
        return validate_decision(decision, context, actions_per_chunk)

    def plan(self, proposal, decision, scope):
        if decision["mode"] == "stop":
            return []
        if decision["mode"] != "student" or type(decision["steps"]) is not int or not 1 <= decision["steps"] <= len(proposal.rows):
            raise ValueError("R1Pro supports a bounded actor prefix or stop")
        return proposal.rows[:decision["steps"]]

    def compile_row(self, row, state):
        return CoordinatedControlStep.model_validate(row)

    def dispatch(self, environment, command, expected_state_version):
        return environment.device_step(DeviceRequest(command=command, expected_state_version=expected_state_version))

    def outcome(self, reply, command):
        count = reply.feedback["steps_executed"]
        if type(count) is not int or count not in (0, 1) or (reply.execution_status == "completed" and count != 1):
            raise ValueError("R1Pro receipt must establish one atomic native sample")
        return StepOutcome(status=reply.execution_status, completed_samples=int(reply.execution_status == "completed"),
            execution_started=count > 0, duration_s=reply.feedback["duration_s"], feedback=reply.feedback,
            stop_reason="native_terminal" if reply.state_after.ended else reply.feedback.get("stop_reason"))


def create_binding(name, args, description, actor_info):
    if name not in (None, BehaviorBinding.name):
        raise ValueError("Select behavior_r1pro_joint for this actor")
    return BehaviorBinding(description, actor_info)
