"""Bind symbolic navigation decisions to bounded native Go2 base control."""
import math
from pathlib import Path

import numpy as np
from PIL import Image

from uni_robot_agent.services.actor.contracts import ActorObservation, HistoryFrame, SymbolicActorProfile, SymbolicPrediction, validate_prediction
from uni_robot_agent.services.actor.images import encode_image
from uni_robot_agent.services.environment.device_contracts import BodyVelocityTarget, CoordinatedControlStep, DeviceEpisodeInfo, DeviceRequest
from uni_robot_agent.tools.hybrid.contracts import HybridCapabilities, Proposal, StepOutcome, YieldToAgent
from uni_robot_agent.tools.hybrid.decisions import validate_decision


def _image(path):
    with Image.open(path) as image:
        return encode_image(np.asarray(image.convert("RGB")))


class NaVILABinding:
    name = "navila_go2"
    action_horizon = 1
    reviewer_supported = True
    reviewer_prompt = Path(__file__).resolve().parents[2] / "resources/prompts/actor/hybrid_decision.md"

    def __init__(self, description, actor_info):
        if not isinstance(description, DeviceEpisodeInfo) or description.integration != "navila":
            raise ValueError("NaVILA binding requires a prepared NaVILA device episode")
        self.description, self.profile = description, actor_info.profile
        devices = description.environment.devices
        if (len(devices) != 1 or devices[0].name != "base" or devices[0].kind != "base"
                or devices[0].control_frame != "body" or "body_velocity" not in devices[0].capabilities):
            raise ValueError("NaVILA requires the declared body-frame locomotion controller")
        profile = self.profile
        if (not isinstance(profile, SymbolicActorProfile) or profile.observation_fields
                or profile.cameras != ["front"] or set(profile.history) != {"front"}
                or profile.history["front"].scope != "episode"
                or profile.history["front"].min_frames != 1
                or set(profile.symbols) != {"move_forward", "turn", "stop"} or profile.symbols["stop"]):
            raise ValueError("Actor must declare complete episode RGB and symbolic navigation semantics")
        expected = {"move_forward": ("distance_m", "m", "forward_displacement"),
                    "turn": ("yaw_rad", "rad", "relative_yaw")}
        for label, (name, unit, semantics) in expected.items():
            fields = profile.symbols[label]
            if len(fields) != 1 or (fields[0].name, fields[0].unit, fields[0].semantics, fields[0].frame) != (name, unit, semantics, "body"):
                raise ValueError("Actor navigation parameters must declare matching units, semantics and frame")
        history = description.environment.metadata["camera_history"]["front"]
        if (history.get("period_ticks"), history.get("first_capture_tick"), history.get("control_dt_s"),
                history.get("include_initial_frame"), history.get("tick_origin")) != (25, 1, .02, True, "post_warmup"):
            raise ValueError("Environment history does not match the pinned native capture schedule")
        max_frames = 2 + (description.environment.metadata["max_episode_steps"] - 1) // 25
        if profile.history["front"].max_frames < max_frames:
            raise ValueError("Actor history limit cannot retain this complete environment episode")
        self.capabilities = HybridCapabilities(groups=("base",),
            constraint_schema={"type": "object", "properties": {}, "additionalProperties": False},
            default_actions=1, max_actions=1, default_max_chunks=6)

    def validate_scope(self, active_groups, constraints):
        if active_groups != ["base"] or not isinstance(constraints, dict) or constraints:
            raise ValueError("NaVILA requires active_groups=['base'] and empty constraints")
        return {"active_groups": ["base"]}

    def encode_observation(self, inputs, instruction):
        history = [HistoryFrame(frame_id=frame.frame_id, captured_at=frame.captured_at,
            simulation_time_s=frame.simulation_time_s, state_version=frame.state_version, image=_image(frame.path))
            for frame in inputs.history["front"]]
        return ActorObservation(instruction=instruction, state=[], images={"front": _image(inputs.images["front"])},
                                history={"front": history})

    def decode_prediction(self, prediction, manifest, images, scope):
        if not isinstance(prediction, SymbolicPrediction):
            raise ValueError("NaVILA binding requires a symbolic prediction")
        validate_prediction(prediction, self.profile)
        if prediction.label == "stop":
            return YieldToAgent(reason="actor_stop", metadata={"prediction": prediction.model_dump(mode="json"),
                "state_version": manifest["state"]["state_version"], "navigation_finished": False})
        if prediction.label == "move_forward":
            distance = prediction.parameters["distance_m"]
            if distance not in (.25, .5, .75):
                raise ValueError("NaVILA forward distances must be 0.25, 0.5 or 0.75 metres")
            duration, forward, yaw = distance / .5, .5, 0.
        else:
            angle = prediction.parameters["yaw_rad"]
            if not any(math.isclose(abs(angle), math.radians(value), abs_tol=1e-9) for value in (15, 30, 45)):
                raise ValueError("NaVILA yaw increments must be 15, 30 or 45 degrees")
            duration, forward, yaw = abs(angle) / (math.pi / 6), 0., math.copysign(math.pi / 6, angle)
        command = CoordinatedControlStep(device="base", duration_s=duration,
            velocities={"base": BodyVelocityTarget(frame="body", linear_m_s=[forward, 0., 0.], angular_rad_s=[0., 0., yaw])})
        raw = prediction.model_dump(mode="json")
        return Proposal(rows=[command.model_dump(mode="json")], state_version=manifest["state"]["state_version"],
            current_state=manifest["state"]["devices"], images=images, raw_actions=raw, scoped_actions=raw,
            metadata={"episode_started": manifest["state"]["state_version"] > 0, "prediction_evidence": prediction.evidence,
                      "interpretation": "Requested displacement from velocity and duration, not measured travel."})

    def preview(self, proposal, environment):
        return {"commands": proposal.rows, "interpretation": proposal.metadata["interpretation"]}

    def review_context(self, proposal, scope):
        return {"current_state": proposal.current_state, "preview": proposal.preview, "scope": scope,
                "episode_started": proposal.metadata["episode_started"]}

    def validate_decision(self, decision, context, actions_per_chunk):
        return validate_decision(decision, context, actions_per_chunk)

    def plan(self, proposal, decision, scope):
        if decision["mode"] == "stop":
            return []
        if decision["mode"] != "student" or type(decision["steps"]) is not int or decision["steps"] != 1:
            raise ValueError("NaVILA review admits one complete navigation segment or stop")
        return proposal.rows

    def compile_row(self, row, state):
        return CoordinatedControlStep.model_validate(row)

    def dispatch(self, environment, command, expected_state_version):
        return environment.device_step(DeviceRequest(command=command, expected_state_version=expected_state_version))

    def outcome(self, reply, command):
        feedback, status = reply.feedback, reply.execution_status
        completed = feedback["samples_completed"]
        if type(completed) is not int or completed not in (0, 1) or (status == "completed") != bool(completed):
            raise ValueError("Navigation receipt does not establish segment completion")
        return StepOutcome(status=status, completed_samples=completed, execution_started=feedback["steps_executed"] > 0,
            duration_s=feedback["duration_s"], stop_reason=feedback.get("stop_reason"), feedback=feedback)


def create_binding(name, args, description, actor_info):
    if name not in (None, NaVILABinding.name):
        raise ValueError("Select navila_go2 for this environment")
    return NaVILABinding(description, actor_info)
