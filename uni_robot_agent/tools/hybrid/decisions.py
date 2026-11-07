"""Common review validation, independent of devices and correction geometry."""
from copy import deepcopy

import jsonschema


def text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} requires nonempty text")


def validate_progress(progress):
    if not isinstance(progress, dict) or set(progress) != {"verified_completed", "currently_attempting", "remaining"}:
        raise ValueError("task_progress requires completed/current/remaining subgoals")
    text(progress["currently_attempting"], "currently_attempting")
    for key in ("verified_completed", "remaining"):
        if not isinstance(progress[key], list):
            raise TypeError(f"task_progress.{key} requires a list")
        for item in progress[key]:
            text(item, key)


def validate_decision(decision, context, actions_per_chunk):
    modes = context["capabilities"]["corrections"]
    if not isinstance(decision, dict) or decision.get("mode") not in {"student", "stop", *modes}:
        raise ValueError("Reviewer chose an unsupported mode")
    mode = decision["mode"]
    extra = modes[mode].get("field") if mode in modes else None
    required = {"request_id", "mode", "steps", "reason", "assessment", "continuation"}
    if extra:
        required.add(extra)
    if set(decision) != required:
        raise ValueError(f"Decision fields must be {sorted(required)}")
    if extra:
        jsonschema.validate(decision[extra], modes[mode]["schema"])
    if decision["request_id"] != context["request_id"]:
        raise ValueError("Stale reviewer request_id")
    text(decision["reason"], "reason")
    continuation = decision["continuation"]
    if continuation not in {"continue", "stop", "phase_complete", "failure"}:
        raise ValueError("Invalid continuation")
    steps = decision["steps"]
    if mode == "stop":
        if type(steps) is not int or steps != 0 or continuation == "continue":
            raise ValueError("stop requires zero steps and a stop continuation")
    else:
        maximum = actions_per_chunk if mode == "student" else min(
            actions_per_chunk, context["capabilities"]["max_correction_actions"])
        if type(steps) is not int or not 1 <= steps <= maximum or continuation != "continue":
            raise ValueError(f"{mode} requires 1..{maximum} steps and continue")
    assessment = decision["assessment"]
    assessment_fields = {"task_progress", "current_subgoal", "execution_status", "execution_evidence",
                         "expected_next_intent", "predicted_next_intent", "intent_status", "intent_evidence"}
    if not isinstance(assessment, dict) or set(assessment) != assessment_fields:
        raise ValueError("Assessment must contain exactly the declared progress, execution and intent fields")
    validate_progress(assessment.get("task_progress"))
    for key in ("current_subgoal", "execution_evidence", "expected_next_intent", "predicted_next_intent", "intent_evidence"):
        text(assessment.get(key), key)
    status, intent = assessment.get("execution_status"), assessment.get("intent_status")
    if status not in {"not_started", "progressing", "failed", "uncertain", "recovered"}:
        raise ValueError("Invalid execution_status")
    if intent not in {"aligned", "misaligned", "uncertain"}:
        raise ValueError("Invalid intent_status")
    if "episode_started" in context and (status != "not_started") != context["episode_started"]:
        raise ValueError("not_started must agree with the observed episode history")
    if mode in modes and status != "failed" and intent != "misaligned":
        raise ValueError("Correction requires observed failure or misaligned intent")
    return deepcopy(decision)
