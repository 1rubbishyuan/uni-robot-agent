"""Map G1 policy semantics to atomic upper-body and locomotion references."""
from pathlib import Path

import numpy as np
from PIL import Image

from uni_robot_agent.services.actor.contracts import ActorField, ActorObservation, ActorProfile, NumericChunk, validate_vector
from uni_robot_agent.services.actor.images import encode_image
from uni_robot_agent.services.environment.device_contracts import (
    BodyHeightTarget, BodyVelocityTarget, CoordinatedControlStep, DeviceEpisodeInfo,
    DeviceRequest, JointPositionTarget,
)
from uni_robot_agent.tools.hybrid.contracts import HybridCapabilities, Proposal, StepOutcome
from uni_robot_agent.tools.hybrid.decisions import validate_decision

from .g1 import CONTROL_DT, CONTROL_GROUPS, IMAGE_CONVENTION, JOINT_GROUPS, OBSERVATION_GROUPS


def actor_profile():
    joints = [ActorField(name=name, unit="rad", semantics="joint_position", control_point=group)
              for group in OBSERVATION_GROUPS for name in JOINT_GROUPS[group]]
    commands = [ActorField(name="base_height", unit="m", semantics="controller_height_reference",
                          frame="ground", control_point="pelvis")]
    commands += [ActorField(name=name, unit=unit, semantics=semantics,
                            frame="base_heading", control_point="pelvis")
                 for name, unit, semantics in (("base_vx", "m/s", "linear_velocity"),
                    ("base_vy", "m/s", "linear_velocity"), ("base_yaw_rate", "rad/s", "angular_velocity"))]
    return ActorProfile(name="arena_g1_locomanipulation", cameras=["head"],
        observation_fields=joints, action_fields=[*joints, *commands], action_horizon=50,
        control_frequency_hz=50, image_convention=IMAGE_CONVENTION)


class ArenaBinding:
    name = "arena_g1_locomanipulation"
    reviewer_supported = True
    reviewer_prompt = Path(__file__).resolve().parents[2] / "resources/prompts/actor/hybrid_decision.md"

    def __init__(self, description, actor_info):
        if not isinstance(description, DeviceEpisodeInfo) or description.integration != "isaaclab_arena":
            raise ValueError("Arena binding requires an isaaclab_arena device episode")
        environment = description.environment
        self.description, self.info, self.profile = description, actor_info, actor_info.profile
        self.devices = {device.name: device for device in environment.devices}
        if self.profile != actor_profile():
            raise ValueError("Actor does not declare the complete matching G1 physical profile")
        if (environment.metadata.get("embodiment") != "g1_wbc_joint"
                or environment.metadata.get("wbc") != "homie_v2"
                or environment.metadata.get("native_step_duration_s") != CONTROL_DT):
            raise ValueError("Arena binding requires the declared 50 Hz G1 Homie joint controller")
        for group, names in JOINT_GROUPS.items():
            device = self.devices.get(group)
            if device is None or device.joint_names != names:
                raise ValueError(f"Arena {group} joints differ from the declared policy order")
            if (device.joint_units != {name: "rad" for name in names}
                    or len(device.joint_limits) != len(names)):
                raise ValueError(f"Arena {group} requires radians and complete native joint limits")
        if (set(self.devices) != {*JOINT_GROUPS, "base", "body", "robot"}
                or self.devices["robot"].kind != "assembly"
                or "coordinated_control" not in self.devices["robot"].capabilities):
            raise ValueError("Arena requires its complete coordinated G1 assembly")
        if "head" not in {camera.name for camera in environment.cameras}:
            raise ValueError("Arena actor requires its native head camera")
        self.capabilities = HybridCapabilities(groups=("robot",),
            constraint_schema={"type": "object", "properties": {}, "additionalProperties": False},
            default_actions=25, max_actions=50, default_max_chunks=6)

    @property
    def action_horizon(self):
        return self.profile.action_horizon

    def validate_scope(self, active_groups, constraints):
        if active_groups != ["robot"] or constraints != {}:
            raise ValueError("G1 requires the coordinated robot group and empty constraints")
        return {"active_groups": ["robot"]}

    def _measured(self, manifest):
        devices = manifest["state"]["devices"]
        values = []
        for group in OBSERVATION_GROUPS:
            joints = devices[group]["joint_positions_rad"]
            if set(joints) != set(JOINT_GROUPS[group]):
                raise ValueError(f"Measured {group} joints differ from the profile")
            values.extend(float(joints[name]) for name in JOINT_GROUPS[group])
        validate_vector(values, self.profile.observation_fields, "observation")
        return values

    def encode_observation(self, inputs, instruction):
        with Image.open(inputs.images["head"]) as image:
            encoded = encode_image(np.asarray(image.convert("RGB")))
        return ActorObservation(instruction=instruction, state=self._measured(inputs.manifest), images={"head": encoded})

    def decode_prediction(self, prediction, manifest, images, scope):
        if not isinstance(prediction, NumericChunk):
            raise ValueError("G1 requires a numerical prediction chunk")
        raw = np.asarray(prediction.actions, dtype=float)
        if raw.shape != (50, 35) or not np.isfinite(raw).all():
            raise ValueError("G1 policy must return fifty finite 35-dimensional samples")
        rows = []
        for row in raw:
            validate_vector(row.tolist(), self.profile.action_fields, "action")
            values = dict(zip((field.name for field in self.profile.action_fields), row.tolist(), strict=True))
            command = CoordinatedControlStep(device="robot", duration_s=CONTROL_DT,
                joints={group: {name: JointPositionTarget(position=values[name], unit="rad")
                                for name in JOINT_GROUPS[group]} for group in CONTROL_GROUPS},
                velocities={"base": BodyVelocityTarget(frame="base_heading",
                    linear_m_s=[values["base_vx"], values["base_vy"], 0.], angular_rad_s=[0., 0., values["base_yaw_rate"]])},
                heights={"body": BodyHeightTarget(frame="ground", height_m=values["base_height"])})
            self._validate_command(command)
            rows.append(command.model_dump(mode="json"))
        return Proposal(rows=rows, state_version=manifest["state"]["state_version"],
            current_state=manifest["state"]["devices"], images=images, raw_actions=raw, scoped_actions=raw.copy(),
            metadata={"action_kind": "upper_body_joints_and_locomotion_references",
                "episode_started": manifest["state"]["completed_commands"] > 0,
                "native_ignored_prediction_fields": JOINT_GROUPS["waist"],
                "native_controlled_joints": "legs_and_waist", "prediction_units": "physical",
                "interpretation": "Raw/scoped policy predictions include waist compatibility channels; executed commands omit them."})

    def _validate_command(self, command):
        if (command.device != "robot" or command.duration_s != CONTROL_DT
                or set(command.joints) != set(CONTROL_GROUPS)
                or set(command.velocities) != {"base"} or set(command.heights) != {"body"}):
            raise ValueError("G1 requires one complete coordinated 50 Hz controller sample")
        velocity = command.velocities["base"]
        if (velocity.frame != "base_heading" or velocity.linear_m_s[2] != 0
                or velocity.angular_rad_s[:2] != (0., 0.) or command.heights["body"].frame != "ground"):
            raise ValueError("G1 requires planar base-heading velocity and a ground-relative height reference")
        for group, targets in command.joints.items():
            if set(targets) != set(JOINT_GROUPS[group]) or any(value.unit != "rad" for value in targets.values()):
                raise ValueError("G1 requires every upper-body joint in radians")
            device = self.devices[group]
            for name, (low, high) in zip(device.joint_names, device.joint_limits, strict=True):
                if not low <= targets[name].position <= high:
                    raise ValueError(f"G1 target {name} exceeds its declared native joint limits")

    def preview(self, proposal, environment):
        return {"controller_references": proposal.rows,
            "interpretation": "Requested joint, planar velocity and height references; not predicted motion, balance or contact."}

    def review_context(self, proposal, scope):
        return {"current_state": proposal.current_state, "preview": proposal.preview, "scope": scope,
                "episode_started": proposal.metadata["episode_started"],
                "device_descriptions": [device.model_dump(mode="json") for device in self.devices.values()]}

    def validate_decision(self, decision, context, actions_per_chunk):
        return validate_decision(decision, context, actions_per_chunk)

    def plan(self, proposal, decision, scope):
        if decision["mode"] == "stop":
            return []
        if decision["mode"] != "student" or type(decision["steps"]) is not int or not 1 <= decision["steps"] <= len(proposal.rows):
            raise ValueError("G1 supports only a bounded student prefix or stop")
        rows = proposal.rows[:decision["steps"]]
        for row in rows:
            self._validate_command(CoordinatedControlStep.model_validate(row))
        return rows

    def compile_row(self, row, state):
        command = CoordinatedControlStep.model_validate(row)
        self._validate_command(command)
        return command

    def dispatch(self, environment, command, expected_state_version):
        return environment.device_step(DeviceRequest(command=command, expected_state_version=expected_state_version))

    def outcome(self, reply, command):
        count, status = reply.feedback["steps_executed"], reply.execution_status
        if type(count) is not int or count not in (0, 1) or (status == "completed" and count != 1):
            raise ValueError("A completed G1 receipt must acknowledge exactly one control sample")
        return StepOutcome(status=status, completed_samples=int(status == "completed"), execution_started=count > 0,
            duration_s=reply.feedback["duration_s"], stop_reason="native_terminal" if reply.state_after.ended else reply.feedback.get("stop_reason"),
            feedback=reply.feedback)


def create_binding(name, args, description, actor_info):
    if name not in (None, ArenaBinding.name):
        raise ValueError("Select arena_g1_locomanipulation for the admitted G1 actor")
    return ArenaBinding(description, actor_info)
