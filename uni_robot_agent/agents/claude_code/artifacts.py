"""Finalize Claude native model records without claiming Codex SFT compatibility."""
from pathlib import Path

from uni_robot_agent.events import ErrorEvent
from uni_robot_agent.provider.recording.exchanges import get_model_exchange_error


def finalize_model_records(gateway, result, session_dir, event_bus):
    records = gateway.trace_records
    result.provider_error = get_model_exchange_error(
        records, interrupted=result.stop_reason in {"max_turns", "wall_time_limit", "cancelled"},
    )
    if result.provider_error:
        result.error = result.error or result.provider_error
        if event_bus:
            event_bus.emit(ErrorEvent(message=result.provider_error))
    if session_dir is not None:
        result.provider_log_path = str(Path(session_dir) / "claude_messages")
        result.train_error = "Claude native model exchanges are archived; provider-faithful SFT export is not implemented"
    gateway.persist_report({
        "trace_format": "claude_messages", "records_frozen": True,
        "exchange_count": len(records), "provider_error": result.provider_error,
        "agent_error": result.error, "stop_reason": result.stop_reason,
        "main_thread_id": result.main_thread_id, "train_error": result.train_error,
        "exchange_persistence_errors": [
            {"index": item["index"], "error": item["persistence_error"]}
            for item in records if item.get("persistence_error")
        ],
    })
