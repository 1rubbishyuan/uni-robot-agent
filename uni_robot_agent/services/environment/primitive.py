"""Primitive physical execution for the environment service.

Direct and hybrid programs compose these primitives in their tool layers.
This module contains only the primitive executor used by the service boundary.
"""

from __future__ import annotations

from collections.abc import Callable
from uni_robot_agent.services.environment.contracts import (
    ActionCommand,
    EnvironmentState,
    JointCommand,
    NativeCommand,
    PathCommand,
    PoseCommand,
    RequestRejected,
)

from .backend import EnvironmentBackend


class PrimitiveExecutor:
    """Forward one protocol command to the native environment adapter.

    This is the service boundary: it owns the physical step and its receipt,
    but it intentionally does not decide how a model-level action such as
    ``move`` or ``grasp`` should be decomposed.  That semantic program
    belongs to the tool layer, which can issue several primitive steps.
    """

    def __init__(self, backend: EnvironmentBackend,
                 on_step: Callable[[EnvironmentState], None] | None = None) -> None:
        self.backend = backend
        self.on_step = on_step

    def step(self, command: ActionCommand) -> dict:
        """Execute exactly one pose, joint, native or whole-path command."""
        if isinstance(command, PoseCommand):
            receipt = self.backend.step_pose(command)
        elif isinstance(command, JointCommand):
            receipt = self.backend.step_joints(command)
        elif isinstance(command, NativeCommand):
            receipt = self.backend.step_native(command)
        elif isinstance(command, PathCommand):
            step_path = getattr(self.backend, "step_path", None)
            if step_path is None:
                raise RequestRejected("This environment does not run whole-path commands")
            receipt = step_path(command)
        else:  # pragma: no cover - the discriminated contract prevents this.
            raise TypeError(f"Unsupported control command: {type(command).__name__}")
        if receipt.accepted and self.on_step is not None:
            self.on_step(receipt.state)
        return {
            "accepted": receipt.accepted,
            "state": receipt.state.model_dump(mode="json"),
            "diagnostics": dict(receipt.diagnostics),
            "execution_outcome": receipt.execution_outcome,
            "stop_confirmed": receipt.stop_confirmed,
            "requires_operator_recovery": receipt.requires_operator_recovery,
            "command": command.model_dump(mode="json"),
        }

__all__ = ["PrimitiveExecutor"]
