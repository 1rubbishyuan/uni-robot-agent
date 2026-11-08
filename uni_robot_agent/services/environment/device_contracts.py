"""Optional device protocol; no scalar gripper or simulated step counters."""

from typing import Annotated, Any, Literal
from pydantic import Field, model_validator
from .contracts import Contract, CameraDescription, FiniteFloat, Pose, RequestIdentity, Vector3


class DeviceDescription(Contract):
    name: str
    kind: Literal["arm", "hand", "assembly"]
    joint_names: list[str] = Field(default_factory=list)
    joint_limits: list[tuple[FiniteFloat, FiniteFloat]] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    control_frame: str | None = None
    home: list[FiniteFloat] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_joints(self):
        if len(set(self.joint_names)) != len(self.joint_names):
            raise ValueError("Device joint names must be unique")
        if self.joint_limits and (
            len(self.joint_limits) != len(self.joint_names) or any(lo > hi for lo, hi in self.joint_limits)
        ):
            raise ValueError("Device limits must match named joints")
        if self.home is not None and len(self.home) != len(self.joint_names):
            raise ValueError("Home must match named joints")
        return self


class DeviceEnvironmentDescription(Contract):
    devices: list[DeviceDescription] = Field(default_factory=list)
    cameras: list[CameraDescription] = Field(default_factory=list)
    world_frame: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_names(self):
        for items in (self.devices, self.cameras):
            names = [item.name for item in items]
            if len(names) != len(set(names)):
                raise ValueError("Descriptor names must be unique")
        return self


class DeviceEpisodeInfo(Contract):
    protocol_version: Literal["devices-1"] = "devices-1"
    tool_schema_version: Literal["dexterous-1"] = "dexterous-1"
    episode_id: str = Field(min_length=1)
    task: str
    environment: DeviceEnvironmentDescription
    capabilities: list[str] = Field(default_factory=list)
    integration: str = "dexterous_hand"
    metadata: dict[str, Any] = Field(default_factory=dict)
    lifecycle: Literal["active", "finishing", "finished", "failed"] = "active"
    next_sequence: int = 1


class DeviceState(Contract):
    kind: Literal["devices"] = "devices"
    devices: dict[str, dict[str, Any]]
    state_version: int = Field(ge=0)
    completed_commands: int = Field(ge=0)
    measured_at: FiniteFloat
    ended: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class JointSegment(Contract):
    kind: Literal["joint_segment"] = "joint_segment"
    device: str
    joint_names: list[str] = Field(min_length=1)
    joint_positions_rad: list[FiniteFloat] = Field(min_length=1)
    duration_s: FiniteFloat = Field(default=2.0, gt=0)

    @model_validator(mode="after")
    def named_targets(self):
        if len(self.joint_names) != len(self.joint_positions_rad) or len(set(self.joint_names)) != len(
            self.joint_names
        ):
            raise ValueError("Provide distinct named targets with matching dimensions")
        return self


class PoseSegment(Contract):
    kind: Literal["pose_segment"] = "pose_segment"
    device: str
    target: Pose | None = None
    translation_m: Vector3 | None = None
    max_step_m: FiniteFloat = Field(default=0.01, gt=0)

    @model_validator(mode="after")
    def one_target(self):
        if (self.target is None) == (self.translation_m is None):
            raise ValueError("Provide one absolute pose or relative translation")
        return self


class DeviceOperation(Contract):
    kind: Literal["device_operation"] = "device_operation"
    device: str
    operation: Literal["enable", "disable", "home", "hold"]
    duration_s: FiniteFloat = Field(default=1.0, gt=0)


class CoordinatedDeviceStep(Contract):
    """Apply named component targets together in one native control step.

    The routing device is an assembly. Its adapter validates membership, frames,
    joint limits and completeness before any component advances.
    """

    kind: Literal["coordinated_step"] = "coordinated_step"
    device: str = Field(min_length=1)
    poses: dict[str, Pose] = Field(default_factory=dict, max_length=64)
    joints: dict[str, dict[str, FiniteFloat]] = Field(default_factory=dict, max_length=64)

    @model_validator(mode="after")
    def named_targets(self):
        if not self.poses and not self.joints:
            raise ValueError("Provide at least one coordinated target")
        if any(not name for name in (*self.poses, *self.joints)):
            raise ValueError("Coordinated targets require nonempty device names")
        if any(not targets or any(not name for name in targets) for targets in self.joints.values()):
            raise ValueError("Joint targets require nonempty named positions")
        return self


DeviceCommand = Annotated[
    JointSegment | PoseSegment | DeviceOperation | CoordinatedDeviceStep,
    Field(discriminator="kind"),
]


class DeviceRequest(Contract):
    command: DeviceCommand
    expected_state_version: int = Field(ge=0, strict=True)


class DeviceReply(Contract):
    identity: RequestIdentity
    state_before: DeviceState
    state_after: DeviceState
    feedback: dict[str, Any]
    snapshot: dict[str, Any] | None = None
    trace: dict[str, Any] | None = None
    artifact_error: str | None = None
    execution_status: Literal["completed", "partial", "rejected", "unknown"] = "completed"


class InterruptRequest(Contract):
    episode_id: str
    request_id: str = Field(min_length=1)
    device: str
    target_request_id: str | None = None


def parse_info(payload):
    from .contracts import EpisodeInfo

    model = DeviceEpisodeInfo if payload.get("protocol_version") == "devices-1" else EpisodeInfo
    return model.model_validate(payload)


def parse_state(payload, info):
    from .contracts import EnvironmentState

    model = DeviceState if isinstance(info, DeviceEpisodeInfo) else EnvironmentState
    return model.model_validate(payload)


def parse_reply(payload, info):
    from .contracts import ActionReply

    model = DeviceReply if isinstance(info, DeviceEpisodeInfo) else ActionReply
    return model.model_validate(payload)
