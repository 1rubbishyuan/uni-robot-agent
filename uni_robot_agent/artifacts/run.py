"""Persist the final evaluation outcome."""
from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

from uni_robot_agent.telemetry.logging import get_logger

logger = get_logger("artifact_store")


def build_run_result(
    args,
    *,
    agent_result,
    error_msg,
    elapsed_s,
    outcome,
    storage_errors=None,
    artifact_storage=None,
) -> dict:
    """Combine the episode outcome, agent result and persistence status into a run record."""
    from uni_robot_agent.config import public_config

    identity = getattr(args, "_episode_identity", None) or {}
    env_meta = {**identity.get("metadata", {}), **outcome.metadata}
    record = {
        "config": public_config(args),
        "task_success": outcome.success,
        "steps": env_meta.get("steps"),
        "score": env_meta.get("score"),
        "accepted": outcome.accepted,
        "retryable": outcome.retryable,
        "failure_category": outcome.failure_category,
        "agent_finished": bool(agent_result.success) if agent_result else False,
        "stop_reason": env_meta.get("stop_reason") or getattr(agent_result, "stop_reason", None),
        "integration": args.integration,
        "agent_backend": args.agent_backend,
        "model": args.model,
        "task": getattr(args, "task", None) or env_meta.get("task_id"),
        "instruction": args.instruction,
        "skill_writing": getattr(args, "skill_writing", False),
        "tool_return_mode": getattr(args, "tool_return_mode", "background"),
        "resource_scheduling": getattr(args, "resource_scheduling", "per-resource"),
        "observation_profile": getattr(args, "observation_profile", "none"),
        "action_profile": getattr(args, "action_profile", None),
        "tool_profile": getattr(args, "tool_profile", None),
        "tool_modules": list(getattr(args, "tool_modules", ())),
        "suite": env_meta.get("suite"),
        "task_id": env_meta.get("task_id", env_meta.get("task")),
        "episode_identity": getattr(args, "_episode_identity", None),
        "deployment": getattr(args, "_deployment", None),
        "seed": env_meta.get("seed"),
        "layout": env_meta.get("layout"),
        "layout_sha256": env_meta.get("layout_sha256"),
        "task_sha256": env_meta.get("task_sha256"),
        "sample_idx": env_meta.get("sample_idx"),
        "turns_used": agent_result.turns_used if agent_result else 0,
        "max_turns": args.max_turns,
        "episode_timeout_s": args.episode_timeout_s,
        "reasoning_effort": args.reasoning_effort,
        "context_policy": getattr(args, "context_policy", "identity"),
        "telemetry": getattr(args, "telemetry", "off"),
        "elapsed_s": round(elapsed_s, 1),
        "error": error_msg or (agent_result.error if agent_result else None),
        "agent_message": agent_result.message[:1000] if agent_result and agent_result.message else None,
        "provider_log_path": agent_result.provider_log_path if agent_result else None,
        "train_path": agent_result.train_path if agent_result else None,
        "train_partial": agent_result.train_partial if agent_result else False,
        "train_stop_reason": agent_result.train_stop_reason if agent_result else None,
        "train_error": agent_result.train_error if agent_result else None,
        "train_debug_path": agent_result.train_debug_path if agent_result else None,
    }
    errors = dict(storage_errors or {})
    if artifact_storage is not None:
        record["artifact_layout_version"] = 1
        record["artifact_storage"] = dict(artifact_storage)
        if artifact_storage.get("close_error") or artifact_storage["failed"] or artifact_storage["pending"]:
            errors["artifacts"] = artifact_storage.get("close_error") or (
                f"{artifact_storage['failed']} artifacts failed; {artifact_storage['pending']} pending"
            )
    if errors:
        record["storage_errors"] = errors
    return record


def write_json_atomic(path, value, *, ensure_ascii=False, allow_nan=True, trailing_newline=False):
    """Publish complete JSON without exposing a partially written destination."""
    path = Path(path)
    text = json.dumps(value, ensure_ascii=ensure_ascii, indent=2, allow_nan=allow_nan)
    if trailing_newline:
        text += "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_result_json(session_dir, record) -> dict:
    path = Path(session_dir) / "result.json"
    record = dict(record)
    for name in ("provider_log_path", "train_path", "train_debug_path"):
        value = record.get(name)
        if value and Path(value).is_absolute():
            record[name] = str(Path(value).relative_to(path.parent.resolve()))
    write_json_atomic(path, record)
    logger.info("Evaluation result: task_success=%s -> %s", record["task_success"], path)
    return record


def read_result_json(path) -> dict:
    record = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(record, dict):
        raise ValueError("Run result must be a JSON object")
    return record
