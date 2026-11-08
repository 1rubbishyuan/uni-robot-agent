"""Optional, typed views of existing model-visible receipts, not tool outputs."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class ReceiptView(BaseModel):
    """Project known fields without rejecting an already executed action's receipt.

    Missing, null or incorrectly typed fields remain unknown. The caller retains
    the original payload, including extra fields and malformed values, separately.
    These views must never be used to validate requests or native service state.
    """

    model_config = ConfigDict(strict=True, extra="ignore", frozen=True)

    @field_validator("*", mode="wrap")
    @classmethod
    def preserve_unknown(cls, value, handler):
        try:
            return handler(value)
        except ValidationError:
            # Formatting cannot invalidate an acknowledged action or cause replay.
            return None


class ArmFeedback(ReceiptView):
    """Direct motion/opening evidence for one participating arm."""

    pose_reached: bool | None = Field(None, description="Grasp-site pose within requested tolerances; not object attachment.")
    actual_pose: list[float] | None = Field(None, description="Measured world grasp-site [x,y,z,roll,pitch,yaw], metres and fixed-axis XYZ radians.")
    position_error_m: float | None = Field(None, description="Measured distance from the original position goal, metres.")
    orientation_error_rad: float | None = Field(None, description="Measured angular error from the original orientation goal, radians.")
    position_tolerance_m: float | None = Field(None, description="Position tolerance used to judge arrival, metres.")
    orientation_tolerance_rad: float | None = Field(None, description="Orientation tolerance used to judge arrival, radians.")
    opening_reached: bool | None = Field(None, description="Measured opening within tolerance; not proof of grasp/contact. None when not requested or unavailable.")
    opening_status: str | None = Field(None, description="Reported opening status, e.g. reached, pending, stalled, unknown, not_requested or unavailable.")
    actual_opening_m: float | None = Field(None, description="Measured signed pad gap, metres, when calibrated.")
    target_opening_m: float | None = Field(None, description="Requested effective pad gap, metres, when calibrated.")
    opening_error_m: float | None = Field(None, description="Absolute error from the opening target, metres.")
    gripper_command_submitted: bool | None = Field(None, description="Whether motion submitted the gripper drive command; not measured opening or contact.")


class MotionFeedback(ReceiptView):
    """Whole-call Direct feedback; other tool families retain their own payloads."""

    pose_reached: bool | None = Field(None, description="All participating arm poses reached their original goals within tolerance.")
    targets_reached: bool | None = Field(None, description="All requested pose and opening targets reached. None if a requested opening cannot be measured.")
    stop_reason: str | None = Field(None, description="Reported stop reason, e.g. pose_reached, planning_rejected, stalled, requested_step_budget or native_end; not task success.")
    stop_evidence: dict | None = Field(None, description="Original stopping diagnostics, including rejecting arm when reported; inspect before deciding recovery.")
    steps_executed: int | None = Field(None, description="Accepted control commands during this call, not the episode total or necessarily native steps.")
    executed_native_steps: int | None = Field(None, description="Environment steps consumed by this call; rejected commands need not consume steps.")
    no_execution: bool | None = Field(None, description="Reported absence of accepted execution; not an automatic retry instruction.")
    arms: dict[str, ArmFeedback] | None = Field(None, description="Per-participant evidence keyed by declared arm name; absent fields remain unknown.")


class ResultState(ReceiptView):
    """Common progress only; full arm/device measurements remain in raw data."""

    state_version: int | None = Field(None, description="Version of the returned state, not a fresh query or the image snapshot ID.")
    native_steps: int | None = Field(None, description="Total environment steps at this receipt, when the environment reports a step counter.")
    ended: bool | None = Field(None, description="Whether the environment has ended; does not disclose official task success.")


class ResultFields(ReceiptView):
    """Common CodeResult attributes projected from the unchanged tool payload."""

    call_ok: bool | None = Field(None, description="Call validity only; True does not establish motion, grasp or task success.")
    error: str | None = Field(None, description="Reported call error; partial physical execution may precede an error.")
    remaining_steps: int | None = Field(None, description="Remaining environment step budget at this receipt, when reported; None is not zero.")
    snapshot_id: str | None = Field(None, description="Identity of the returned observation, when present; use capture_scene for localization calibration.")
    artifact_error: str | None = Field(None, description="Observation/artifact failure after execution; do not replay the action to recover images.")
    state: ResultState | None = Field(None, description="Returned state progress. Full measured arm/device state is in result.data['state'].")
    feedback: MotionFeedback | None = Field(None, description="Direct action feedback when present. Hybrid execution/assessment and perception results remain in result.data.")
