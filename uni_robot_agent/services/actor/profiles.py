"""Model-independent observation and action profiles for supported embodiments."""

from .contracts import ActorProfile

X5_CAMERAS = ("head", "left_wrist", "right_wrist")
X5_FIELDS = tuple(
    field
    for arm in ("left", "right")
    for field in (*(f"{arm}_joint_{i}" for i in range(6)), f"{arm}_gripper")
)


def x5_joint_profile(cameras: list[str], horizon: int) -> ActorProfile:
    """Absolute X5 joint targets at 25 Hz, with normalized scalar grippers."""
    if not cameras or not set(cameras) <= set(X5_CAMERAS):
        raise ValueError("X5 actor cameras must select head, left_wrist or right_wrist")
    return ActorProfile(
        name="robodojo_x5_joint",
        cameras=cameras,
        state_fields=list(X5_FIELDS),
        action_fields=list(X5_FIELDS),
        action_horizon=horizon,
        state_units=(["rad"] * 6 + ["normalized"]) * 2,
        action_units=(["rad"] * 6 + ["normalized"]) * 2,
        coordinate_frame="arx_x5_joint_absolute_25hz",
        gripper_convention="0=closed,1=open; continuous normalized opening",
        image_convention="RGB HWC; model preprocessing belongs to the upstream adapter",
    )


def validate_x5_joint_profile(profile: ActorProfile) -> None:
    """Reject equal-sized vectors with different control semantics."""
    expected = x5_joint_profile(profile.cameras, profile.action_horizon)
    if profile != expected:
        raise ValueError("Actor must declare the complete robodojo_x5_joint profile")
