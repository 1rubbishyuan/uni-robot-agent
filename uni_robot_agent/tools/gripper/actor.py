"""Observe, review and execute bounded action chunks for one episode."""
from __future__ import annotations

from uuid import uuid4

from uni_robot_agent.artifacts.actor import ActorArchive
from uni_robot_agent.services.environment.contracts import CaptureRequest, RequestRejected
from uni_robot_agent.tools.gripper.contracts import MAX_PREFIX_COMMANDS
from uni_robot_agent.tools.gripper.reviewer import build_review_input


def actor_action_limits(binding):
    """Bound executed prefixes by the service output, not the network's internal horizon."""
    maximum = min(binding.action_horizon, MAX_PREFIX_COMMANDS)
    return min(25, maximum), maximum


class ActorRunner:
    """Own chunk progression; binding and reviewer lifetimes belong to the session."""

    def __init__(self, episode, binding, reviewer, execute_prefix):
        self.episode = episode
        self.binding = binding
        self.reviewer = reviewer
        self.execute_prefix = execute_prefix
        self.previous_chunk = None

    def run(self, instruction, active_arms, actions_per_chunk=None, max_chunks=None,
            gripper_targets=None, task_progress=None):
        episode = self.episode
        episode.require_active()
        if not isinstance(instruction, str) or not instruction.strip():
            raise RequestRejected("instruction must be nonempty")
        binding, reviewer = self.binding, self.reviewer
        if binding is None:
            raise RequestRejected("actor_act is unavailable in this action profile")
        default_actions, maximum_actions = actor_action_limits(binding)
        if actions_per_chunk is None:
            actions_per_chunk = default_actions
        if type(actions_per_chunk) is not int or not 1 <= actions_per_chunk <= maximum_actions:
            raise RequestRejected(f"actions_per_chunk must be an integer in 1..{maximum_actions}")
        if max_chunks is None:
            max_chunks = binding.default_max_chunks
        if type(max_chunks) is not int or max_chunks < 1:
            raise RequestRejected("max_chunks must be a positive integer")
        arms = {item.name for item in episode.description.environment.arms}
        if not active_arms or len(set(active_arms)) != len(active_arms) or not set(active_arms) <= arms:
            raise RequestRejected("active_arms must name distinct available arms")
        gripper_targets = gripper_targets or {}
        if not set(gripper_targets) <= set(active_arms):
            raise RequestRejected("gripper_targets must address active arms")
        session_id = uuid4().hex
        initial_steps = episode.state.native_steps
        summary = {"session_id": session_id, "instruction": instruction, "chunks": [], "chunks_executed": 0,
                   "executed_actions": 0, "stop_reason": "max_chunks", "execution_uncertain": False,
                   "error": None, "artifact_error": None}
        archive = ActorArchive(episode.output, session_id, episode.storage_errors)

        try:
            for index in range(max_chunks):
                episode.require_active()
                manifest = episode.env.capture(CaptureRequest(high_resolution=False))
                proposal = binding.prepare(episode, manifest, instruction, active_arms, gripper_targets)
                state = manifest["state"]
                previous = self.previous_chunk
                context, images = build_review_input(
                    episode=episode, proposal=proposal, previous=previous, state=state,
                    session_id=session_id, index=index, max_chunks=max_chunks,
                    actions_per_chunk=actions_per_chunk, instruction=instruction,
                    task_progress=task_progress, active_arms=active_arms, gripper_targets=gripper_targets,
                )
                record = {"context": context, "review_images": images, "proposal": proposal["metadata"]}

                archive.write_chunk(index, record, proposal=proposal)
                decision = reviewer.decide(context=context, images=images, actions_per_chunk=actions_per_chunk)
                record["decision"] = decision
                archive.write_chunk(index, record)
                before = state["native_steps"]
                feedback = None
                try:
                    if decision["mode"] == "stop":
                        feedback = {"executed_actions": 0, "stop_reason": decision["continuation"]}
                        episode.latest_snapshot = manifest
                    else:
                        commands = binding.commands(proposal, decision, active_arms, gripper_targets)
                        # Each command is one primitive request; ACKs update episode state.
                        reply = self.execute_prefix(commands,
                            expected_state_version=state["state_version"], mode=decision["mode"])
                        episode.publish(reply)
                        record["execution"] = reply.model_dump(mode="json")
                        feedback = reply.feedback
                finally:
                    # Preserve confirmed actions even if a later ACK or archive write fails.
                    executed = episode.state.native_steps - before
                    if feedback is None and executed:
                        feedback = episode.latest_feedback
                    summary["chunks"].append({"chunk_index": index, "decision": decision, "feedback": feedback,
                        "before_step": before, "after_step": episode.state.native_steps, "executed_actions": executed})
                    summary["chunks_executed"] += int(executed > 0)
                    if feedback is not None:
                        self.previous_chunk = {"request_id": context["request_id"], "assessment": decision.get("assessment"),
                            "mode": decision["mode"], "images": proposal["images"], "after_step_id": episode.state.native_steps,
                            "feedback": feedback}
                archive.write_chunk(index, record)
                if episode.state.ended:
                    summary["stop_reason"] = "native_terminal"
                    break
                if feedback.get("tracking_interrupted"):
                    summary["stop_reason"] = "tracking_interrupted"
                    break
                if decision["continuation"] != "continue":
                    summary["stop_reason"] = decision["continuation"]
                    break
                if executed < decision["steps"]:
                    summary["stop_reason"] = "partial_execution"
                    break
        except Exception as error:
            summary.update(stop_reason="error", error_type=type(error).__name__)
            summary["error"] = archive.error or f"Actor execution failed: {type(error).__name__}: {error}"
        finally:
            summary["executed_actions"] = episode.state.native_steps - initial_steps
            summary["execution_uncertain"] = episode.env.uncertain
            summary["artifact_error"] = archive.artifact_error
            archive.write_summary(summary)
            summary["artifact_error"] = archive.artifact_error
        return summary
