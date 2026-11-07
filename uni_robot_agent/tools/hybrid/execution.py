"""One bounded execution loop over already validated proposal rows."""
from __future__ import annotations

from uni_robot_agent.services.environment.client import EnvironmentRequestError

from .contracts import ExecutionResult, HybridBinding


def execute_prefix(episode, binding: HybridBinding, rows: list, *, expected_state_version: int) -> ExecutionResult:
    result = ExecutionResult(requested_samples=len(rows))
    start_state = episode.state.model_copy(deep=True)
    try:
        episode.require_active()
        if episode.state.state_version != expected_state_version:
            raise ValueError("Stale action proposal; capture a fresh observation")
        for row in rows:
            if episode.state.ended:
                result.stop_reason = "native_terminal"
                break
            # Compile from the last real ACK, never a predicted successor state.
            command = binding.compile_row(row, episode.state)
            reply = binding.dispatch(episode.env, command, episode.state.state_version)
            # Acknowledged state survives interpretation and storage failures.
            result.last_reply = reply
            result.acknowledged_commands += 1
            episode.accept_primitive(reply)
            outcome = binding.outcome(reply, command)
            result.completed_samples += outcome.completed_samples
            result.execution_started |= outcome.execution_started or outcome.completed_samples > 0 or outcome.status == "partial"
            if outcome.duration_s is None:
                result.duration_known = False
            else:
                result.duration_s += outcome.duration_s
            result.status, result.feedback = outcome.status, outcome.feedback
            if outcome.status != "completed" or outcome.stop_reason:
                result.stop_reason = outcome.stop_reason or outcome.status
                break
        if result.completed_samples < len(rows) and result.status == "completed":
            result.status = "partial"
    except Exception as error:
        failure_status = error.error.execution_status if isinstance(error, EnvironmentRequestError) else None
        uncertain = bool(episode.env.uncertain) or failure_status == "unknown" or getattr(error, "execution_uncertain", False)
        result.status = "unknown" if uncertain else "partial" if (
            result.execution_started or failure_status == "partial") else "rejected"
        result.execution_started |= failure_status == "partial"
        if result.last_reply is not None and result.acknowledged_commands > result.completed_samples:
            # An uninterpretable receipt cannot establish whether its command ran.
            result.status = "unknown"
        result.stop_reason = "execution_unknown" if result.status == "unknown" else "execution_error"
        result.error = f"{type(error).__name__}: {error}"
        if result.status in {"partial", "unknown"}:
            result.duration_known = False
    if result.status == "unknown":
        # The shared gate also protects a subsequent Direct tool call.
        episode.env.uncertain = True
        result.duration_known = False
    if result.last_reply is not None:
        try:
            aggregate = result.last_reply.model_copy(update={"state_before": start_state, "feedback": result.public()})
            episode.publish(aggregate)
        except Exception as error:
            # Publication failure cannot erase physical acknowledgements.
            result.error = f"{type(error).__name__}: {error}"
            result.stop_reason = "publication_error"
    return result
