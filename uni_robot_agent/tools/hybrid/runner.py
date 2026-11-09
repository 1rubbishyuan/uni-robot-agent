"""Observe, infer, review and execute without assumptions about robot devices."""
from __future__ import annotations

from dataclasses import asdict
from uuid import uuid4

from uni_robot_agent.artifacts.actor import ActorArchive
from uni_robot_agent.execution import current_execution_id
from uni_robot_agent.execution.correlation import current_call
from uni_robot_agent.services.environment.contracts import (
    CaptureRequest,
    RequestRejected,
)

from .contracts import YieldToAgent
from .observations import ObservationInputs
from .execution import execute_prefix


def actor_action_limits(binding):
    maximum = min(binding.action_horizon, binding.capabilities.max_actions)
    return min(binding.capabilities.default_actions, maximum), maximum


class ActorRunner:
    def __init__(self, episode, *, actor, binding, reviewer):
        self.episode, self.actor = episode, actor
        self.binding, self.reviewer = binding, reviewer
        self.previous_chunk = None

    def run(self, instruction, active_groups, actions_per_chunk=None, max_chunks=None,
            constraints=None, task_progress=None):
        episode, binding = self.episode, self.binding
        episode.require_active()
        if binding is None or self.actor is None:
            raise RequestRejected("actor_act is unavailable without a configured actor and binding")
        if not isinstance(instruction, str) or not instruction.strip():
            raise RequestRejected("instruction must be nonempty")
        default, maximum = actor_action_limits(binding)
        actions_per_chunk = default if actions_per_chunk is None else actions_per_chunk
        max_chunks = binding.capabilities.default_max_chunks if max_chunks is None else max_chunks
        if type(actions_per_chunk) is not int or not 1 <= actions_per_chunk <= maximum:
            raise RequestRejected(f"actions_per_chunk must be in 1..{maximum}")
        if type(max_chunks) is not int or max_chunks < 1:
            raise RequestRejected("max_chunks must be a positive integer")
        constraints = {} if constraints is None else constraints
        scope = binding.validate_scope(active_groups, constraints)
        session_id = uuid4().hex
        summary = {"session_id": session_id, "execution_id": current_execution_id(), **current_call(), "instruction": instruction, "chunks": [], "chunks_executed": 0,
                   "executed_actions": 0, "acknowledged_commands": 0, "execution_started": False,
                   "duration_s": 0., "stop_reason": "max_chunks", "execution_uncertain": False,
                   "error": None, "artifact_error": None}
        archive = ActorArchive(episode.output, session_id, episode.storage_errors)
        stage = "actor"
        try:
            for index in range(max_chunks):
                stage = "actor"
                episode.require_active()
                manifest = episode.env.capture(CaptureRequest(high_resolution=False))
                inputs = ObservationInputs.capture(episode, manifest)
                paths = inputs.images
                observation = binding.encode_observation(inputs, instruction)
                prediction = self.actor.predict(observation)
                proposal = binding.decode_prediction(prediction.prediction, manifest, paths, scope)
                info = self.actor.info
                actor_metadata = {"inference_index": prediction.sequence,
                    "checkpoint_sha256": prediction.checkpoint_sha256,
                    "model": info.model, "model_id": info.model_id, "runtime": info.runtime,
                    "model_config_name": info.model_config_name, "profile": info.profile.model_dump(mode="json")}
                if isinstance(proposal, YieldToAgent):
                    feedback = {"executed_actions": 0, "acknowledged_commands": 0, "execution_started": False,
                                "execution_uncertain": False, "duration_s": 0., "stop_reason": "actor_yield",
                                "reason": proposal.reason, "no_execution": True}
                    context = {"session_id": session_id, "chunk_index": index,
                               "previous_actor": self.previous_chunk, "actor": actor_metadata}
                    archive.write_chunk(index, {"context": context, "review_images": paths,
                        "prediction": prediction.prediction.model_dump(mode="json"),
                        "yield": {"reason": proposal.reason, "metadata": proposal.metadata}, "feedback": feedback})
                    episode.latest_snapshot = manifest
                    summary["chunks"].append({"chunk_index": index, "decision": None, "feedback": feedback,
                        "yield": {"reason": proposal.reason, "metadata": proposal.metadata},
                        "observed_state_version": manifest["state_version"],
                        "after_state_version": episode.state.state_version})
                    summary["stop_reason"] = "actor_yield"
                    self.previous_chunk = {"assessment": None, "mode": "yield", "images": paths,
                        "state_version": episode.state.state_version, "feedback": feedback}
                    break
                proposal.preview = binding.preview(proposal, episode.env)
                proposal.metadata.update(actor_metadata)
                previous = self.previous_chunk
                context = {**binding.review_context(proposal, scope),
                    "request_id": uuid4().hex, "session_id": session_id, "chunk_index": index,
                    "execution_id": current_execution_id(), **current_call(),
                    "max_chunks": max_chunks, "actions_per_chunk": actions_per_chunk,
                    "global_instruction": episode.instruction, "local_instruction": instruction,
                    "task_progress": task_progress if index == 0 else
                        (previous.get("assessment") or {}).get("task_progress") if previous else None,
                    "progress_source": "main_agent" if index == 0 and task_progress else "previous_controller",
                    "progress_is_model_reported": True, "actor": proposal.metadata,
                    "previous_actor": previous, "previous_feedback": episode.latest_feedback,
                    "recent_observations": episode.recent_observations[-6:],
                    "capabilities": asdict(binding.capabilities), "active_groups": active_groups,
                    "constraints": constraints, "state_version": proposal.state_version}
                if episode.task_requirements:
                    context["additional_task_requirements"] = episode.task_requirements
                images = {"current/" + name: path for name, path in paths.items()}
                if previous:
                    images.update({"previous/" + name: path for name, path in previous["images"].items()})
                record = {"context": context, "review_images": images, "proposal": proposal.metadata,
                          "prediction": prediction.prediction.model_dump(mode="json")}
                archive.write_chunk(index, record, proposal=proposal)
                stage = "reviewer"
                decision = self.reviewer.decide(context=context, images=images, actions_per_chunk=actions_per_chunk,
                    record=lambda attempt, evidence, index=index: archive.write_review(index, attempt, evidence))
                stage = "actor"
                record["decision"] = decision
                archive.write_chunk(index, record)
                if decision["mode"] == "stop":
                    result = None
                    episode.latest_snapshot = manifest
                    feedback = {"executed_actions": 0, "acknowledged_commands": 0,
                                "execution_started": False, "stop_reason": decision["continuation"]}
                else:
                    # The entire decision is checked before the first physical command.
                    rows = binding.plan(proposal, decision, scope)
                    if len(rows) != decision["steps"]:
                        raise ValueError("Compiled plan does not match the selected sample budget")
                    result = execute_prefix(episode, binding, rows, expected_state_version=proposal.state_version)
                    feedback = result.public()
                summary["executed_actions"] += feedback["executed_actions"]
                summary["acknowledged_commands"] += feedback["acknowledged_commands"]
                summary["execution_started"] |= feedback["execution_started"]
                summary["execution_uncertain"] |= feedback.get("execution_uncertain", False)
                if summary["duration_s"] is not None:
                    summary["duration_s"] = (summary["duration_s"] + feedback.get("duration_s", 0.)
                                             if feedback.get("duration_s", 0.) is not None else None)
                summary["chunks_executed"] += int(feedback["execution_started"])
                summary["chunks"].append({"chunk_index": index, "decision": decision, "feedback": feedback,
                    "observed_state_version": proposal.state_version,
                    "after_state_version": episode.state.state_version})
                self.previous_chunk = {"request_id": context["request_id"], "assessment": decision.get("assessment"),
                    "mode": decision["mode"], "images": paths, "state_version": episode.state.state_version, "feedback": feedback}
                record["feedback"] = feedback
                if result is not None and result.last_reply is not None:
                    record["execution"] = result.last_reply.model_dump(mode="json")
                archive.write_chunk(index, record)
                if feedback.get("error"):
                    summary.update(error=feedback["error"], stop_reason=feedback["stop_reason"])
                    break
                if episode.state.ended:
                    summary["stop_reason"] = "native_terminal"
                    break
                if decision["continuation"] != "continue":
                    summary["stop_reason"] = decision["continuation"]
                    break
                if feedback.get("execution_status") != "completed" or feedback.get("stop_reason") != "requested_sample_budget":
                    summary["stop_reason"] = feedback["stop_reason"]
                    break
        except Exception as error:  # noqa: BLE001 - preserve partial execution and archive its outcome
            summary.update(stop_reason="error", error_type=type(error).__name__, error_stage=stage)
            summary["error"] = archive.error or f"{stage.capitalize()} failed: {type(error).__name__}: {error}"
        finally:
            summary["execution_uncertain"] |= episode.env.uncertain
            summary["artifact_error"] = archive.artifact_error
            archive.write_summary(summary)
            summary["artifact_error"] = archive.artifact_error
        return summary
