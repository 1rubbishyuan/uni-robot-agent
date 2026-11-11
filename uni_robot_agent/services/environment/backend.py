"""Native environment boundary; clients import only wire contracts."""
from typing import Any, Protocol

from .contracts import (
    GoalRegionRequest,
    GoalRegionReply,
    ActionCommand,
    EnvironmentDescription,
    EnvironmentState,
    JointCommand,
    NativeCommand,
    PathCommand,
    PoseCommand,
    StepReceipt,
)
from .snapshots import CapturedObservation


class EnvironmentBackend(Protocol):
    """Native operations run on the environment's owner thread when required.

    A step receives the complete environment command. The adapter validates all arms
    before advancing physics. It owns no multi-step tool loops or file encoding.
    """
    description: EnvironmentDescription

    def read_state(self) -> EnvironmentState: ...
    def step_pose(self, command: PoseCommand) -> StepReceipt: ...
    def step_joints(self, command: JointCommand) -> StepReceipt: ...
    def step_native(self, command: NativeCommand) -> StepReceipt: ...
    def validate_commands(self, commands: list[ActionCommand], mode: str) -> None: ...
    def preview_joints(self, commands: list[JointCommand]) -> dict[str, Any]: ...
    def capture(self, cameras: list[str] | None = None, *, high_resolution: bool = False) -> CapturedObservation: ...
    def finish(self, reason: str, status: str | None = None) -> dict[str, Any]: ...
    def close(self) -> None: ...


class PathBackend(Protocol):
    """Optional whole-path capability, declared by environment metadata ``motion_execution: wholepath``."""
    def step_path(self, command: PathCommand) -> StepReceipt: ...


class GoalRegionBackend(Protocol):
    """Optional solve-only capability; not required by other integrations."""
    def solve_goal_region(self, request: GoalRegionRequest) -> GoalRegionReply: ...


class DeviceBackend(Protocol):
    """Native device operations, separate from the scalar-gripper backend."""
    def read_state(self): ...
    def validate_device(self, command) -> None: ...
    def preview_device(self, command) -> dict: ...
    def execute_device(self, command, *, cancellation) -> dict: ...
    def interrupt_device(self, device: str) -> dict: ...
