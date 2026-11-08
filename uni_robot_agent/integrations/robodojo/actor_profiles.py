"""Explicit model-independent profiles for supported observation/control semantics."""

from uni_robot_agent.services.actor.contracts import ActorField, ActorProfile

X5_CAMERAS = ("head", "left_wrist", "right_wrist")
X5_FIELDS = tuple(
    field
    for arm in ("left", "right")
    for field in (*(f"{arm}_joint_{i}" for i in range(6)), f"{arm}_gripper")
)


def x5_fields(*, bounded_gripper: bool = True) -> list[ActorField]:
    """Measured or absolute-target X5 joints and normalized scalar openings."""
    fields = []
    for arm in ("left", "right"):
        fields.extend(ActorField(name=f"{arm}_joint_{index}", unit="rad",
            semantics="joint_position", frame="arx_x5_joint") for index in range(6))
        fields.append(ActorField(name=f"{arm}_gripper", unit="normalized",
            semantics="normalized_opening", minimum=0 if bounded_gripper else None,
            maximum=1 if bounded_gripper else None))
    return fields


def x5_joint_profile(cameras: list[str], horizon: int) -> ActorProfile:
    """Absolute X5 joint targets at 25 Hz, with normalized scalar grippers."""
    if not cameras or not set(cameras) <= set(X5_CAMERAS):
        raise ValueError("X5 actor cameras must select head, left_wrist or right_wrist")
    return ActorProfile(
        name="robodojo_x5_joint", cameras=cameras,
        observation_fields=x5_fields(), action_fields=x5_fields(),
        action_horizon=horizon, control_frequency_hz=25,
        image_convention="RGB HWC; model preprocessing belongs to the upstream adapter",
    )


def validate_x5_joint_profile(profile: ActorProfile) -> None:
    """Reject equal-sized vectors with different control semantics."""
    expected = x5_joint_profile(profile.cameras, profile.action_horizon)
    if profile != expected:
        raise ValueError("Actor must declare the complete robodojo_x5_joint profile")

CAMERAS = ("cam_high", "cam_left_wrist", "cam_right_wrist")


def profile_for(name: str) -> ActorProfile:
    if name == "robodojo_pi05":
        return ActorProfile(
            name=name,
            cameras=list(CAMERAS),
            observation_fields=x5_fields(),
            # The pinned legacy binding clips finite model gripper predictions.
            action_fields=x5_fields(bounded_gripper=False),
            action_horizon=50,
            control_frequency_hz=25,
            image_convention="RGB HWC; pinned runtime resizes with padding to 224 and converts to CHW",
        )
    raise ValueError(f"Unknown Pi0.5 profile: {name}")
