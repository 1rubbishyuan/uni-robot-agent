"""Independent model-facing actor collaboration tools."""
from uni_robot_agent.execution.scheduler import ToolExecutionPolicy
from uni_robot_agent.tools.presentation import state_for_model
from uni_robot_agent.tools.scene.perception import snapshot_images, snapshot_metadata
from uni_robot_agent.tools.toolkit import (
    ToolOutput,
    ToolProvider,
    ToolSpec,
    bind_tools,
    schema,
)

from .runner import ActorRunner, actor_action_limits

NAMES = ("actor_act",)


def tool_policies():
    # Direct and actor tools mutate the same world and must share its exclusion.
    return {"actor_act": ToolExecutionPolicy(resource_key="episode", return_mode="background",
        concurrency="exclusive", mutates_world=True)}


def tool_specs(binding=None, reviewer=None):
    groups = {"type": "array", "items": {"type": "string"}, "minItems": 1, "uniqueItems": True}
    budget = {"type": "integer", "minimum": 1}
    chunks = {"type": "integer", "minimum": 1}
    constraints = schema({})
    notes = "No actor binding is configured; this tool is unavailable."
    if binding is not None:
        capabilities = binding.capabilities
        default, maximum = actor_action_limits(binding)
        groups["items"]["enum"] = list(capabilities.groups)
        groups["maxItems"] = len(capabilities.groups)
        budget.update(default=default, maximum=maximum)
        chunks["default"] = capabilities.default_max_chunks
        constraints = capabilities.constraint_schema
        modes = ["student", "stop", *capabilities.corrections]
        notes = (f"Reviewer is {'enabled' if reviewer and reviewer.enabled else 'disabled'}. "
                 f"Supported review modes: {', '.join(modes)}. actions_per_chunk defaults to {default}, "
                 f"maximum {maximum}; max_chunks defaults to {capabilities.default_max_chunks}.")
    spec = ToolSpec("actor_act",
        "Execute a bounded local phase with the configured actor. Each chunk captures fresh observations, predicts, "
        "reviews when enabled, and executes only the allowed prefix. Unused predictions are discarded. Keep the "
        "instruction and active control groups fixed during this call. Constraints apply throughout, including "
        "corrections. Do not insert Direct actions between internal chunks. Inspect confirmed execution, partial or "
        "unknown outcomes, stop_reason and latest observations; completion is not task success. " + notes,
        schema({"instruction": {"type": "string", "minLength": 1}, "active_groups": groups,
                "constraints": constraints, "actions_per_chunk": budget, "max_chunks": chunks,
                "task_progress": schema({"verified_completed": {"type": "array", "items": {"type": "string"}},
                    "currently_attempting": {"type": "string"}, "remaining": {"type": "array", "items": {"type": "string"}}},
                    ("verified_completed", "currently_attempting", "remaining"))}, ("instruction", "active_groups")))
    return [spec]


def unavailable_tools(episode):
    """Declare the disabled tool for explicit visibility ablations only."""
    def unavailable(**arguments):
        from uni_robot_agent.services.environment.contracts import RequestRejected
        episode.require_active()
        raise RequestRejected("actor_act is unavailable without a configured actor and binding")
    return bind_tools(tool_specs(), provider_id="hybrid",
                      handlers={"actor_act": unavailable}, invoke=episode.invoke)


class HybridTools(ToolProvider):
    provider_id = "hybrid"

    def __init__(self, episode, *, actor, binding, reviewer):
        if actor is None or binding is None:
            raise ValueError("HybridTools requires a configured actor and binding")
        self.episode, self.binding, self.reviewer = episode, binding, reviewer
        self.runner = ActorRunner(episode, actor=actor, binding=binding, reviewer=reviewer)

    def get_tools(self):
        return bind_tools(tool_specs(self.binding, self.reviewer), provider_id=self.provider_id,
                          handlers={"actor_act": self.actor_act}, invoke=self.invoke)

    def invoke(self, handler, **arguments):
        return self.episode.invoke(handler, **arguments)

    def actor_act(self, **arguments):
        summary = self.runner.run(**arguments)
        chunk = summary["chunks"][-1] if summary["chunks"] else None
        decision = chunk["decision"] if chunk else {}
        assessment = decision.get("assessment") or {}
        images, metadata, image_error = [], {}, None
        try:
            if self.episode.latest_snapshot is not None:
                images = snapshot_images(self.episode.env, self.episode.latest_snapshot, mirror=self.episode.artifact_mirror)
                if images:
                    metadata = snapshot_metadata(self.episode.latest_snapshot, [image.name for image in images],
                                                 mirror=self.episode.artifact_mirror)
        except Exception as error:  # noqa: BLE001 - surface optional image persistence failures
            image_error = f"{type(error).__name__}: {error}"
        uncertain = summary["execution_uncertain"]
        return ToolOutput(success=summary["error"] is None, error=summary["error"], extra={
            "state": state_for_model(self.episode.state.model_dump(mode="json")),
            "remaining_steps": self.episode.remaining_steps,
            "execution": {**{key: summary[key] for key in ("executed_actions", "chunks_executed", "acknowledged_commands",
                "execution_started", "duration_s", "stop_reason", "execution_uncertain")},
                "no_execution": not summary["execution_started"] and not uncertain},
            "assessment": {"observed_state_version": chunk["observed_state_version"] if assessment else None,
                "execution_status": assessment.get("execution_status"), "execution_evidence": assessment.get("execution_evidence"),
                "stop_evidence": summary["error"] or (decision.get("reason") if decision.get("mode") == "stop" else None)},
            **metadata, "image_cameras": [image.name for image in images],
            "error_stage": summary.get("error_stage"),
            "artifact_error": "; ".join(filter(None, (summary["artifact_error"], image_error))) or None}, images=images)
