"""The replanning execution mode: parallel scheduling that stops an old plan after a failed action.

``ReplanningToolExecutor`` keeps every ``parallel`` behavior (background jobs, per-resource or
global-serial scheduling) and adds three rules for world-mutating tools:

1. A new action on a resource waits until the last action's result has been read.
2. When an action that may have moved something does not complete (``ToolOutput.action``),
   queued world-mutating calls on that resource are cancelled before dispatch and keep their
   receipts; a call that was already dequeued is refused when it starts.
3. New world-mutating calls on the resource are refused (``reason: replan_required``) until the
   failed result has been read, the environment has confirmed that the actuators stopped, and
   a ``replan_observation`` tool (on any resource) that started after both returns a new
   snapshot. Tools whose policy sets ``ends_episode`` stay allowed, so the model can still report.

When execution is unknown or the stop is not confirmed, the requirement never clears within the
episode: recovery belongs to the operator. Nothing here dispatches, retries or replays an action.
"""

from __future__ import annotations

import threading
from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Any

from uni_robot_agent.execution import ExecutionResult
from uni_robot_agent.execution.parallel import ParallelToolExecutor
from uni_robot_agent.execution.scheduler import ToolExecutionPolicy
from uni_robot_agent.tools.toolkit import ActionOutcome, Toolkit, ToolOutput

CANCELLED = {"no_execution": True, "execution_status": "cancelled_before_dispatch",
             "reason": "replan_after_failure"}


def action_outcome(output: ToolOutput) -> ActionOutcome | None:
    """The outcome a tool reported; a failed tool that did not say counts as unknown."""
    if output.action is not None:
        return output.action
    if output.success and not output.error:
        return None
    if output.extra.get("no_execution") is True:
        return ActionOutcome(complete=False, executed="none")
    return ActionOutcome(complete=False, executed="unknown")


def requires_replan(outcome: ActionOutcome | None) -> bool:
    """An incomplete action that may have moved something invalidates the queued plan."""
    return outcome is not None and not outcome.complete and outcome.executed != "none"


@dataclass
class PendingReplan:
    epoch: int
    failed_job_id: str | None
    reason: str
    executed: str
    stop_confirmed: bool
    result_read: bool
    failed_snapshot_id: str | None
    cancelled_jobs: list[dict[str, Any]] = field(default_factory=list)

    @property
    def phase(self) -> str:
        if not self.result_read:
            return "result_unread"
        if not self.stop_confirmed:
            return "stop_unconfirmed"
        return "observation_required"

    def report(self, observation_tools: list[str]) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "failed_job_id": self.failed_job_id,
            "reason": self.reason,
            "executed": self.executed,
            "stop_confirmed": self.stop_confirmed,
            "cancelled_jobs": deepcopy(self.cancelled_jobs),
            "next_action": self._next_action(observation_tools),
        }

    def _next_action(self, observation_tools: list[str]) -> str:
        if self.phase == "result_unread":
            return f"Read the result of job {self.failed_job_id} with wait_for_tool or get_tool_result."
        if self.phase == "stop_unconfirmed":
            return ("The stop was not confirmed or execution is unknown. No new action can run until "
                    "an operator recovers the robot; report the condition.")
        if not observation_tools:
            return "No observation tool can start a replan in this environment; report the condition."
        return (f"Call {' or '.join(observation_tools)} for a new observation, then plan a NEW action "
                "from the measured state. Do not resubmit cancelled actions.")


class ReplanTracker:
    """Per-resource replan requirements; the executor holds its admission lock around every call."""

    def __init__(self, observation_tools: list[str]):
        self.observation_tools = observation_tools
        self.pending: dict[str, PendingReplan] = {}
        self.epochs: dict[str, int] = {}

    def epoch(self, resource: str) -> int:
        return self.epochs.get(resource, 0)

    def begin(self, resource: str, job_id: str | None, output: ToolOutput, outcome: ActionOutcome,
              cancelled: list[dict[str, Any]], *, result_read: bool) -> ToolOutput:
        """Start (or extend) a replan requirement and attach it to the failed result."""
        epoch = self.epochs[resource] = self.epoch(resource) + 1
        stop_confirmed = outcome.stopped is True and outcome.executed != "unknown"
        previous = self.pending.get(resource)
        if previous is not None:
            # A later failure cannot erase an earlier unknown execution or unconfirmed stop.
            stop_confirmed = stop_confirmed and previous.stop_confirmed
            cancelled = previous.cancelled_jobs + cancelled
        item = self.pending[resource] = PendingReplan(
            epoch=epoch, failed_job_id=job_id, reason=output.error or "action_incomplete",
            executed=outcome.executed, stop_confirmed=stop_confirmed,
            result_read=result_read or job_id is None,
            failed_snapshot_id=output.extra.get("snapshot_id"), cancelled_jobs=cancelled)
        # The model sees this report by reading the result, so it describes the step after that.
        output.extra["replan"] = replace(item, result_read=True).report(self.observation_tools)
        return output

    def result_read(self, resource: str, job_id: str) -> None:
        item = self.pending.get(resource)
        if item is not None and item.failed_job_id == job_id:
            item.result_read = True

    def rejection(self, resource: str) -> ToolOutput | None:
        item = self.pending.get(resource)
        if item is None:
            return None
        return ToolOutput(success=False, error="A failed action requires a new plan first",
                          extra={"no_execution": True, "reason": "replan_required",
                                 "replan": item.report(self.observation_tools)})

    def observation_tokens(self) -> dict[str, int]:
        """Taken when an observation starts: the replans it may clear, by resource and epoch.

        An observation sees the whole scene, so it can clear a replan on any resource; only one
        that started after a replan's prerequisites can clear that replan.
        """
        return {resource: item.epoch for resource, item in self.pending.items()
                if item.result_read and item.stop_confirmed}

    def observed(self, tokens: dict[str, int] | None, output: ToolOutput) -> ToolOutput:
        if not self.pending:
            return output
        snapshot_id = output.extra.get("snapshot_id")
        usable = output.success and not output.error and bool(snapshot_id)
        cleared = [resource for resource, item in self.pending.items()
                   if usable and (tokens or {}).get(resource) == item.epoch and snapshot_id != item.failed_snapshot_id]
        last = None
        for resource in cleared:
            last = self.pending.pop(resource)
        if self.pending:
            output.extra["replan"] = next(iter(self.pending.values())).report(self.observation_tools)
        else:
            output.extra["replan"] = {
                "phase": "ready_to_replan", "failed_job_id": last.failed_job_id, "snapshot_id": snapshot_id,
                "next_action": "Plan a NEW action from this observation. Do not resubmit cancelled actions."}
        return output


class ReplanningToolExecutor(ParallelToolExecutor):
    """``parallel`` scheduling plus result-read and replan-after-failure admission."""

    def __init__(self, toolkit: Toolkit, policies: dict[str, ToolExecutionPolicy] | None = None, *,
                 require_result_read: bool = True, **options) -> None:
        super().__init__(toolkit, policies, **options)
        # With it, a background action waits for the last result on its resource to be read, so
        # the model plans each action from the previous one's outcome. Without it, several actions
        # can be queued, and a failure cancels the rest.
        self._require_result_read = require_result_read
        # Admission, result reads and replan state change together across concurrent calls.
        self._admission_lock = threading.RLock()
        self._unread_actions: dict[str, str] = {}
        self._replan = ReplanTracker(sorted(
            name for name, policy in self._policies.items()
            if policy.replan_observation and name in toolkit.get_tool_names()))

    @staticmethod
    def _gated(policy: ToolExecutionPolicy) -> bool:
        """World-mutating work that a failure stops and a pending replan refuses."""
        return policy.mutates_world and not policy.ends_episode

    def _admit(self, name: str, arguments: dict[str, Any], *, background: bool) -> ExecutionResult:
        if name in self._control_names or name not in self._toolkit.get_tool_names():
            return self._call(name, arguments, background=background)
        policy = self._policies.get(name, ToolExecutionPolicy())
        if not policy.mutates_world:
            return self._call(name, arguments, background=background)
        tracks_read = self._require_result_read and (background or self._tool_return_mode == "background")
        resource = self._resource_key(policy)
        waits = not background and (self._tool_return_mode == "immediate" or policy.return_mode == "immediate")
        inline = waits and policy.concurrency == "parallel" and self._resource_scheduling != "global-serial"
        # Check and submit under one lock so two concurrent calls cannot both pass admission.
        with self._admission_lock:
            refused = self._admission_refusal(policy, resource, tracks_read=tracks_read)
            if refused is not None:
                self._toolkit.record_result(name, arguments, refused)
                return ExecutionResult.completed(refused)
            if not inline:
                result = self._call(name, arguments, background=background, defer_wait=True)
                if tracks_read and result.job_id:
                    self._unread_actions[resource] = result.job_id
        if inline:
            # It runs on this thread; holding the lock would stall every other admission and result.
            return self._call(name, arguments)
        # The worker needs the admission lock to finish, so wait only after releasing it.
        if result.job_id and waits:
            return self._wait_immediate(result.job_id)
        return result

    def _admission_refusal(self, policy, resource, *, tracks_read) -> ToolOutput | None:
        if self._gated(policy) and (refused := self._replan.rejection(resource)) is not None:
            return refused
        if tracks_read and (job_id := self._unread_actions.get(resource)):
            return ToolOutput(success=False, error="The previous action's result has not been read", extra={
                "no_execution": True, "reason": "unread_action_result", "blocked_by_job_id": job_id,
                "next_action": f"Read job {job_id} with wait_for_tool or get_tool_result first."})
        return None

    def _result_read(self, job: Any) -> None:
        with self._admission_lock:
            if self._unread_actions.get(job.resource_key) == job.job_id:
                del self._unread_actions[job.resource_key]
            self._replan.result_read(job.resource_key, job.job_id)

    def _on_submit(self, policy: ToolExecutionPolicy) -> Any:
        # A job submitted before a failure must not run after it.
        with self._admission_lock:
            return {"epoch": self._replan.epoch(self._resource_key(policy))}

    def _on_start(self, policy: ToolExecutionPolicy, context: Any) -> Any:
        with self._admission_lock:
            epoch = (context or {}).get("epoch", self._replan.epoch(self._resource_key(policy)))
            # Only an observation that starts after the replan prerequisites can clear it.
            token = self._replan.observation_tokens() if policy.replan_observation else None
        return {"epoch": epoch, "token": token}

    def _execute(self, name: str, arguments: dict[str, Any], policy: ToolExecutionPolicy,
                 job_id: str | None, context: Any) -> ToolOutput:
        resource = self._resource_key(policy)
        if self._gated(policy):
            with self._admission_lock:
                if context["epoch"] != self._replan.epoch(resource) or resource in self._replan.pending:
                    output = ToolOutput(success=False, error="Cancelled before dispatch: an earlier action failed",
                                        extra=dict(CANCELLED))
                    self._toolkit.record_result(name, arguments, output)
                    return output

        def annotate(output: ToolOutput) -> ToolOutput:
            return self._after_result(policy, job_id, context["token"], output)

        try:
            return self._toolkit.execute_tool(name, arguments, result_transform=annotate)
        except Exception as exc:
            if not self._gated(policy):
                raise
            output = annotate(ToolOutput(success=False, error=f"{type(exc).__name__}: {exc}",
                                         extra={"execution_uncertain": True}))
            self._toolkit.record_result(name, arguments, output)
            return output

    def _after_result(self, policy: ToolExecutionPolicy, job_id: str | None, token: dict[str, int] | None,
                      output: ToolOutput) -> ToolOutput:
        resource = self._resource_key(policy)
        with self._admission_lock:
            outcome = action_outcome(output) if self._gated(policy) else None
            if requires_replan(outcome):
                names = {name for name, other in self._policies.items()
                         if self._gated(other) and self._resource_key(other) == resource}
                cancelled = self._scheduler.cancel_pending(resource, names, reason=CANCELLED["reason"])
                for job in cancelled:
                    self._result_read(job)
                # A caller that waits for this result reads it as it returns.
                return self._replan.begin(resource, job_id, output, outcome,
                                          [dict(job.summary(), **job.cancellation) for job in cancelled],
                                          result_read=policy.return_mode != "background")
            if policy.replan_observation:
                return self._replan.observed(token, output)
        return output
