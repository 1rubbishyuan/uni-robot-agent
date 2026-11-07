"""Arm and scalar-opening adaptation for compatible actor profiles."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import jsonschema
import numpy as np
from PIL import Image

from uni_robot_agent.services.actor.contracts import ActorObservation, validate_vector
from uni_robot_agent.services.actor.images import encode_image
from uni_robot_agent.services.environment.contracts import ArmCommand, JointCommand, JointTarget, Pose, PoseCommand, PreviewRequest, StepRequest
from uni_robot_agent.tools.gripper.opening import native_opening
from uni_robot_agent.tools.hybrid.contracts import HybridCapabilities, Proposal, StepOutcome
from uni_robot_agent.tools.hybrid.corrections.cartesian import (
    edit_pose, quaternion, rotate_world, rotation_distance, validate_target, vector,
)
from uni_robot_agent.tools.hybrid.decisions import validate_decision


@dataclass(frozen=True)
class ArmActionLayout:
    joints: tuple[str, ...] = ()
    position: tuple[str, ...] = ()
    quaternion: tuple[str, ...] = ()
    rotation_vector: tuple[str, ...] = ()
    gripper: str | None = None


class ArmGripperBinding:
    reviewer_supported = True
    reviewer_prompt = Path(__file__).resolve().parents[3] / "resources/prompts/actor/hybrid_decision.md"

    def __init__(self, *, name, description, actor_info, action_layout: dict[str, ArmActionLayout],
                 observation_encoder: Callable[[dict], list[float]], camera_map=None, action_kind="joint",
                 delta_reference=None, clip_grippers=False, default_actions=25, max_actions=40, default_max_chunks=6):
        self.name, self.description, self.info = name, description, actor_info
        self.profile, self.layout = actor_info.profile, action_layout
        self.observation_encoder, self.action_kind = observation_encoder, action_kind
        self.delta_reference, self.clip_grippers = delta_reference, clip_grippers
        self.arms = {arm.name: arm for arm in description.environment.arms}
        self.controls = {name: arm.control for name, arm in self.arms.items()}
        if set(action_layout) != set(self.arms) or any(control is None for control in self.controls.values()):
            raise ValueError("Actor layout and explicit native capabilities must cover every arm")
        if action_kind not in {"joint", "cartesian_absolute", "cartesian_delta"}:
            raise ValueError("Unsupported arm action representation")
        if action_kind == "cartesian_delta" and delta_reference not in {"observation", "latest_ack"}:
            raise ValueError("Cartesian deltas require an explicit observation or latest_ack reference")
        if action_kind != "cartesian_delta" and delta_reference is not None:
            raise ValueError("Absolute actions do not have a delta reference")
        if abs(self.profile.control_frequency_hz - description.environment.control_frequency_hz) > 1e-8:
            raise ValueError("Actor sample frequency must match the native control frequency")
        self.camera_map = camera_map or {name: name for name in self.profile.cameras}
        available = {camera.name for camera in description.environment.cameras}
        if set(self.camera_map) != set(self.profile.cameras) or not set(self.camera_map.values()) <= available:
            raise ValueError("Actor camera mapping does not match available observations")
        self.indices = {field.name: index for index, field in enumerate(self.profile.action_fields)}
        used = []
        for arm, layout in action_layout.items():
            used.extend((*layout.joints, *layout.position, *layout.quaternion, *layout.rotation_vector))
            if layout.gripper is not None:
                used.append(layout.gripper)
            command = "joints" if action_kind == "joint" else "pose"
            if command not in self.controls[arm].commands:
                raise ValueError(f"{arm} does not support {command} actions")
            if action_kind == "joint":
                if len(layout.joints) != len(self.arms[arm].joint_names) or layout.position or layout.quaternion or layout.rotation_vector:
                    raise ValueError("Joint layout must match the environment joint order and dimensions")
            else:
                orientation_valid = (len(layout.quaternion) == 4 and not layout.rotation_vector
                    if action_kind == "cartesian_absolute" else len(layout.rotation_vector) == 3 and not layout.quaternion)
                if self.controls[arm].rotation == "fixed" and not layout.quaternion and not layout.rotation_vector:
                    orientation_valid = True
                if layout.joints or len(layout.position) != 3 or not orientation_valid:
                    raise ValueError("Cartesian layout must explicitly declare position and orientation fields")
        if len(set(used)) != len(used) or set(used) != set(self.indices):
            raise ValueError("Action layout must use every actor field exactly once")
        self.opening_models = description.environment.metadata.get("gripper_opening_models", {})
        if self.opening_models and set(self.opening_models) != set(self.arms):
            raise ValueError("Metric opening calibration must cover every arm")
        self.opening_key = "gripper_openings_m" if self.opening_models else "gripper_targets"
        opening_properties = {arm: {"type": "number", "minimum": 0,
            "maximum": self.opening_models[arm]["max_opening_m"] if self.opening_models else 1}
            for arm in self.arms if self.controls[arm].gripper != "fixed"}
        constraints = {"type": "object", "properties": {self.opening_key: {
            "type": "object", "properties": opening_properties, "additionalProperties": False}}, "additionalProperties": False}
        can_pose = all("pose" in control.commands for control in self.controls.values())
        can_preview = action_kind == "cartesian_absolute" or (
            action_kind == "joint" and all(control.joint_preview for control in self.controls.values()))
        self.capabilities = HybridCapabilities(groups=tuple(self.arms), constraint_schema=constraints,
            corrections=correction_descriptions(self.controls, edit=can_preview) if can_pose else {}, default_actions=default_actions,
            max_actions=max_actions, default_max_chunks=default_max_chunks)

    @property
    def action_horizon(self):
        return self.profile.action_horizon

    def validate_scope(self, active_groups, constraints):
        if (not isinstance(active_groups, list) or not active_groups or len(set(active_groups)) != len(active_groups)
                or not set(active_groups) <= set(self.arms)):
            raise ValueError("active_groups must name distinct available control groups")
        jsonschema.validate(constraints, self.capabilities.constraint_schema)
        openings, requests = {}, {}
        for arm, value in constraints.get(self.opening_key, {}).items():
            if arm not in active_groups:
                raise ValueError("Opening constraints must address active groups")
            if self.opening_models:
                value, requests[arm] = native_opening(value, self.opening_models[arm])
            if self.controls[arm].gripper == "binary" and value not in (0, 1):
                raise ValueError("This control group supports only binary opening targets")
            openings[arm] = value
        return {"active_groups": active_groups, "openings": openings, "opening_requests": requests}

    def encode_observation(self, manifest, instruction, images):
        values = list(self.observation_encoder(manifest["state"]))
        validate_vector(values, self.profile.observation_fields, "observation")
        encoded = {}
        for slot, camera in self.camera_map.items():
            with Image.open(images[camera]) as image:
                encoded[slot] = encode_image(np.asarray(image.convert("RGB")))
        return ActorObservation(instruction=instruction, state=values, images=encoded)

    def _values(self, row, fields):
        return [float(row[self.indices[name]]) for name in fields]

    def decode_prediction(self, actions, manifest, images, scope):
        raw = np.asarray(actions, dtype=float)
        if raw.shape != (self.action_horizon, len(self.indices)) or not np.isfinite(raw).all():
            raise ValueError("Actor returned an invalid action matrix")
        scoped = raw.copy()
        current = manifest["state"]["arms"]
        rows = []
        for index, values in enumerate(scoped):
            validate_vector(values.tolist(), self.profile.action_fields, "action")
            row = {}
            for arm, layout in self.layout.items():
                state, control = current[arm], self.controls[arm]
                if layout.gripper is None:
                    opening = state["gripper_command"]
                else:
                    opening = float(values[self.indices[layout.gripper]])
                    if self.clip_grippers:
                        opening = float(np.clip(opening, 0, 1))
                    if not 0 <= opening <= 1:
                        raise ValueError("Actor opening must be normalized in [0,1]")
                    if control.gripper == "binary" and opening not in (0, 1):
                        raise ValueError("Actor must declare decoded binary gripper commands")
                    if control.gripper == "fixed" and abs(opening - state["gripper_command"]) > 1e-8:
                        raise ValueError("Actor cannot command an unavailable gripper")
                if arm not in scope["active_groups"]:
                    if "joints" in control.commands:
                        row[arm] = {"joints": list(state["joints"]), "gripper_opening": state["gripper_command"]}
                    else:
                        row[arm] = {"position": list(state["control_pose"]["position"]),
                            "quaternion_wxyz": state["metadata"]["target_quaternion"] if control.rotation_reference == "target"
                                else list(state["control_pose"]["quaternion"]), "gripper_opening": state["gripper_command"]}
                else:
                    opening = scope["openings"].get(arm, opening)
                    row[arm] = {"gripper_opening": opening}
                    if self.action_kind == "joint":
                        row[arm]["joints"] = self._values(values, layout.joints)
                    elif self.action_kind == "cartesian_absolute":
                        row[arm].update(position=self._values(values, layout.position),
                            quaternion_wxyz=self._values(values, layout.quaternion) if layout.quaternion else
                            state["metadata"]["target_quaternion"] if control.rotation_reference == "target"
                            else state["control_pose"]["quaternion"])
                        quaternion(row[arm]["quaternion_wxyz"])
                    else:
                        row[arm].update(delta_position=self._values(values, layout.position),
                            delta_rotation_vector=self._values(values, layout.rotation_vector) if layout.rotation_vector else [0., 0., 0.],
                            reference=self.delta_reference, observation_pose=state["control_pose"])
                if layout.gripper is not None:
                    values[self.indices[layout.gripper]] = row[arm]["gripper_opening"]
                if layout.joints and arm not in scope["active_groups"]:
                    for field, value in zip(layout.joints, state["joints"], strict=True):
                        values[self.indices[field]] = value
                elif arm not in scope["active_groups"]:
                    position = [0., 0., 0.] if self.action_kind == "cartesian_delta" else row[arm]["position"]
                    for field, value in zip(layout.position, position, strict=True):
                        values[self.indices[field]] = value
                    for field in layout.rotation_vector:
                        values[self.indices[field]] = 0.
                    for field, value in zip(layout.quaternion, row[arm].get("quaternion_wxyz", []), strict=False):
                        values[self.indices[field]] = value
            # All rows are checked before execution; compilation has no side effects.
            self._compile(row, current)
            rows.append(row)
        return Proposal(rows=rows, state_version=manifest["state"]["state_version"], current_state=current,
            images=images, raw_actions=raw, scoped_actions=scoped,
            metadata={"action_kind": self.action_kind, "opening_requests": scope["opening_requests"],
                      "episode_started": manifest["state"]["native_steps"] > 0})

    def preview(self, proposal, environment):
        if self.action_kind == "joint" and all(control.joint_preview for control in self.controls.values()):
            commands = [self._compile(row, proposal.current_state) for row in proposal.rows[:50]]
            return environment.preview(PreviewRequest(commands=commands, expected_state_version=proposal.state_version))
        if self.action_kind == "cartesian_absolute":
            trajectory = []
            for row in proposal.rows[:50]:
                command = self._compile(row, proposal.current_state)
                trajectory.append({name: {"position": list(value.pose.position), "quaternion_wxyz": list(value.pose.quaternion),
                    "gripper_opening": value.gripper} if isinstance(value, ArmCommand) else {
                    "position": proposal.current_state[name]["control_pose"]["position"],
                    "quaternion_wxyz": proposal.current_state[name]["control_pose"]["quaternion"], "gripper_opening": value.gripper}
                    for name, value in command.arms.items()})
            return {"trajectory": trajectory, "interpretation": "Requested world control-point targets; not predicted physical or object motion."}
        return {"interpretation": "No future Cartesian trajectory is advertised for this action representation."}

    def review_context(self, proposal, scope):
        return {"current_state": proposal.current_state, "preview": proposal.preview,
            "episode_started": proposal.metadata["episode_started"],
            "arm_descriptions": [arm.model_dump(mode="json") for arm in self.arms.values()],
            "controls": {name: control.model_dump(mode="json") for name, control in self.controls.items()},
            "opening_requests": scope["opening_requests"], "scope": scope}

    def validate_decision(self, decision, context, actions_per_chunk):
        result = validate_decision(decision, context, actions_per_chunk)
        validate_correction(result, context["current_state"], self.controls, context["active_groups"])
        return result

    def plan(self, proposal, decision, scope):
        if decision["mode"] == "student":
            return proposal.rows[:decision["steps"]]
        if decision["mode"] not in self.capabilities.corrections:
            raise ValueError("Unsupported correction mode")
        validate_correction(decision, proposal.current_state, self.controls, scope["active_groups"])
        rows = corrected_targets(proposal, decision, scope["active_groups"], scope["openings"], self.controls)
        for row in rows:
            self._compile(row, proposal.current_state)
        return rows

    def _compile(self, row, state):
        targets = {}
        pose_used = False
        for arm, value in row.items():
            if "joints" in value:
                targets[arm] = JointTarget(joints=value["joints"], gripper=value["gripper_opening"])
                continue
            pose_used = True
            if "delta_position" in value:
                base = state[arm]["control_pose"] if value["reference"] == "latest_ack" else value["observation_pose"]
                position = (np.asarray(base["position"]) + value["delta_position"]).tolist()
                if self.controls[arm].rotation == "fixed":
                    if np.linalg.norm(value["delta_rotation_vector"]) > 1e-10:
                        raise ValueError("Fixed orientation requires zero predicted rotation delta")
                    orientation = (state[arm]["metadata"]["target_quaternion"]
                        if self.controls[arm].rotation_reference == "target" else base["quaternion"])
                else:
                    orientation = rotate_world(base["quaternion"], value["delta_rotation_vector"])
            else:
                position, orientation = value["position"], value["quaternion_wxyz"]
            control = self.controls[arm]
            if control.rotation == "fixed":
                reference = (state[arm]["metadata"]["target_quaternion"] if control.rotation_reference == "target"
                             else state[arm]["control_pose"]["quaternion"])
                if rotation_distance(orientation, reference) > 1e-6:
                    raise ValueError("Action violates fixed orientation capability")
                orientation = reference
            targets[arm] = ArmCommand(pose=Pose(position=position, quaternion=orientation), gripper=value["gripper_opening"])
        if not pose_used:
            return JointCommand(arms=targets)
        solvers = {control.pose_solver for control in self.controls.values()}
        if len(solvers) != 1:
            raise ValueError("A coordinated pose command requires a common native solver")
        return PoseCommand(arms=targets, solver=solvers.pop())

    def compile_row(self, row, state):
        return self._compile(row, state.model_dump(mode="json")["arms"])

    def dispatch(self, environment, command, expected_state_version):
        return environment.step(StepRequest(command=command, expected_state_version=expected_state_version))

    def outcome(self, reply, command):
        feedback = reply.feedback
        accepted = bool(feedback.get("accepted", True))
        diagnostics = feedback.get("diagnostics", feedback)
        status = reply.execution_status
        if not accepted:
            return StepOutcome(status="rejected", stop_reason="planning_rejected", feedback={"stop_evidence": diagnostics})
        commanded = diagnostics.get("commanded_joints", {})
        if "applied_joint_positions" in diagnostics and len(self.arms) == 1:
            commanded = {next(iter(self.arms)): diagnostics["applied_joint_positions"]}
        if not commanded and isinstance(command, JointCommand):
            commanded = {name: value.joints for name, value in command.arms.items()}
        errors, maximum = {}, 0.
        for name, joints in commanded.items():
            error = float(np.max(np.abs(np.asarray(reply.state_after.arms[name].joints) - joints)))
            maximum = max(maximum, error)
            tolerance = self.arms[name].joint_tracking_tolerance_rad
            if tolerance is not None and error > tolerance:
                errors[name] = {"tracking_error_rad": error, "tracking_tolerance_rad": tolerance}
        return StepOutcome(status=status, completed_samples=int(status == "completed"), execution_started=True,
            duration_s=1. / self.profile.control_frequency_hz if status == "completed" else None,
            stop_reason="tracking_interrupted" if errors else "native_terminal" if reply.state_after.ended else None,
            feedback={"tracking_interrupted": bool(errors), "max_tracking_error_rad": maximum,
                      **({"stop_evidence": {"arms": errors}} if errors else {})})


def validate_correction(decision, current, controls, active_groups):
    mode = decision["mode"]
    if mode not in {"edit", "eef"}:
        return
    values = decision["edit" if mode == "edit" else "target"]
    if not isinstance(values, dict) or set(values) != set(controls):
        raise ValueError("Corrections must explicitly describe every declared control group")
    for name, value in values.items():
        control = controls[name]
        if not isinstance(value, dict):
            raise ValueError("A correction must be an object")
        if mode == "edit":
            if set(value) != {"delta_position", "delta_rotation_vector", "gripper"}:
                raise ValueError("Invalid edit fields")
            dp = vector(value["delta_position"], 3, "delta_position")
            dr = vector(value["delta_rotation_vector"], 3, "delta_rotation_vector")
            if np.linalg.norm(dp) > .05 + 1e-10 or np.linalg.norm(dr) > .35 + 1e-10:
                raise ValueError("Edit exceeds 0.05 metres or 0.35 radians")
            if value["gripper"] not in {"keep", "open", "closed"}:
                raise ValueError("Invalid gripper edit")
            if control.rotation == "fixed" and np.linalg.norm(dr) > 1e-10:
                raise ValueError("This control group does not support rotation edits")
            if control.gripper == "fixed" and value["gripper"] != "keep":
                raise ValueError("This control group has no controllable gripper")
            if name not in active_groups and (np.any(dp) or np.any(dr) or value["gripper"] != "keep"):
                raise ValueError("Inactive control groups must not be edited")
        else:
            if set(value) != {"position", "quaternion_wxyz", "gripper_closed"} or type(value["gripper_closed"]) is not bool:
                raise ValueError("Invalid EEF target fields")
            validate_target(value, current[name]["control_pose"])
            if control.rotation == "fixed":
                reference = (current[name]["metadata"]["target_quaternion"] if control.rotation_reference == "target"
                             else current[name]["control_pose"]["quaternion"])
                if rotation_distance(value["quaternion_wxyz"], reference) > 1e-6:
                    raise ValueError("This control group must retain its declared rotation reference")
            if control.gripper == "fixed" and value["gripper_closed"] != (current[name]["gripper_command"] < .5):
                raise ValueError("This control group must retain its gripper state")


def corrected_targets(proposal, decision, active_groups, openings, controls):
    """Latch absolute targets once; execution never reapplies a delta."""
    targets = []
    steps = decision["steps"]
    for index in range(steps):
        row = {}
        for name, measured in proposal.current_state.items():
            if name not in active_groups:
                if "joints" in controls[name].commands:
                    row[name] = {"joints": list(measured["joints"]), "gripper_opening": measured["gripper_command"]}
                else:
                    row[name] = {"position": list(measured["control_pose"]["position"]),
                        "quaternion_wxyz": measured["metadata"]["target_quaternion"] if controls[name].rotation_reference == "target"
                            else list(measured["control_pose"]["quaternion"]), "gripper_opening": measured["gripper_command"]}
                continue
            if decision["mode"] == "eef":
                target = deepcopy(decision["target"][name])
                target["gripper_opening"] = 0. if target.pop("gripper_closed") else 1.
            else:
                original = proposal.preview["trajectory"][index][name]
                edit = decision["edit"][name]
                alpha = (index + 1) / steps
                target = {**edit_pose(original, edit["delta_position"], edit["delta_rotation_vector"], alpha),
                    "gripper_opening": original["gripper_opening"] if edit["gripper"] == "keep"
                        else 0. if edit["gripper"] == "closed" else 1.}
            if name in openings:
                target["gripper_opening"] = openings[name]
            row[name] = target
        targets.append(row)
    return targets


def correction_descriptions(controls, *, edit):
    modes = {"eef": {"field": "target", "description":
        "For every group provide position, unit quaternion_wxyz and gripper_closed. Targets use the registered world "
        "control point and must be within 0.05 m and 0.35 rad of its measured pose. The same absolute target is held; "
        "the environment recomputes its native control from each measured acknowledgement."}}
    if edit:
        modes["edit"] = {"field": "edit", "description":
            "For every group provide delta_position, delta_rotation_vector and gripper=keep/open/closed. "
            "World offsets ramp from 1/steps to 1 across the preview prefix; rotation multiplies on the left. "
            "Norm limits are 0.05 m and 0.35 rad. keep retains continuous predicted opening. Fixed-rotation groups "
            "require zero rotation edits; inactive groups require zero offsets and keep. Gripper overrides apply "
            "from the first action. Targets are latched once, not accumulated after execution."}
    vector_schema = {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}
    quaternion_schema = {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4}
    for mode, specification in modes.items():
        properties = {}
        for name, control in controls.items():
            fields = ({"position": vector_schema, "quaternion_wxyz": quaternion_schema,
                       "gripper_closed": {"type": "boolean"}} if mode == "eef" else {
                "delta_position": vector_schema,
                "delta_rotation_vector": {"const": [0, 0, 0]} if control.rotation == "fixed" else vector_schema,
                "gripper": {"enum": ["keep"] if control.gripper == "fixed" else ["keep", "open", "closed"]}})
            properties[name] = {"type": "object", "properties": fields, "required": list(fields), "additionalProperties": False}
        specification["schema"] = {"type": "object", "properties": properties,
                                   "required": list(properties), "additionalProperties": False}
    return modes
