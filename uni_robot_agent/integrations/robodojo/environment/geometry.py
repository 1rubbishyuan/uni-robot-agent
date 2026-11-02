"""RoboDojo native RGB/depth capture and optical-to-world calibration.

Isolate compatibility with private RoboDojo/Isaac camera interfaces here.
Ordinary observations capture RGB only. Calibrated capture optionally attaches
depth annotators; RGB-only episodes never attach or read them.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from .native_compat import native_call
from .camera_projection import ProjectionUnavailable, attach_geometry_projection, geometry_projection

CAMERA_ALIASES = {
    "head": ("cam_head", "cam_high", "head_camera", "top_camera"),
    "left_wrist": ("cam_left_wrist", "left_camera", "left_wrist"),
    "right_wrist": ("cam_right_wrist", "right_camera", "right_wrist"),
}
CAMERA_NAMES = tuple(CAMERA_ALIASES)
_OPTICAL_FROM_USD = np.diag([1.0, -1.0, -1.0])


def capture_geometry(
    env: Any,
    *,
    slot: int,
    cameras: list[str],
    depth_enabled: bool = True,
    calibration_enabled: bool = True,
    native_call=native_call,
) -> dict[str, dict[str, Any]]:
    """Capture under the integration's existing serialized-capture lock.

    Keep native calls separate so the pool main loop can service other rooms.
    Only temporary projection-reader operations need an explicit owner call.
    """
    if depth_enabled and not calibration_enabled:
        raise ValueError("Disabling camera calibration requires depth_enabled=False")
    camera_manager = _required_attr(env, "camera_manager")
    actual_names = list(camera_manager.camera_names[slot])
    selected = [
        (alias, _camera_id(alias, actual_names))
        for alias in _normalized_cameras(cameras)
    ]

    # Running tiled render products cannot reliably add render variables. Use
    # Isaac Camera's public API for a separate geometry product: resume and attach
    # depth during capture, then detach and pause to avoid overhead on ordinary VLA observations.
    captures: list[tuple[str, int, Any]] = []
    parameters = {}
    try:
        for alias, camera_id in selected:
            camera = camera_manager.cameras[slot][camera_id]
            if getattr(camera, "_uni_robot_agent_geometry_initialized", False):
                camera.resume()
            else:
                camera.initialize()
                camera._uni_robot_agent_geometry_initialized = True
            captures.append((alias, camera_id, camera))
            if depth_enabled:
                camera.add_distance_to_image_plane_to_frame()
            if calibration_enabled:
                parameters[alias] = native_call(env, lambda native: attach_geometry_projection(
                    native.camera_manager.cameras[slot][camera_id]))

        # New annotators may need several frames across Isaac versions. render()
        # refreshes sensors without advancing physics, so waiting does not move the scene.
        frames: dict[str, tuple[np.ndarray, np.ndarray | None]] = {}
        projections = {}
        projection_errors = {}
        for _ in range(8):
            env.render()
            for alias, camera_id, camera in captures:
                rgb_value = camera.get_rgb()
                depth_value = camera.get_depth() if depth_enabled else None
                if rgb_value is None or (depth_enabled and depth_value is None):
                    continue
                rgb = _to_numpy(rgb_value)
                depth = np.squeeze(_to_numpy(depth_value)) if depth_enabled else None
                if rgb.size and (depth is None or depth.size):
                    if calibration_enabled:
                        # A resumed camera can retain RGB before its newly attached
                        # projection reader has produced data. Wait for both.
                        try:
                            projection = native_call(env, lambda native: geometry_projection(
                                native.camera_manager.cameras[slot][camera_id], parameters[alias], rgb.shape[:2]))
                        except ProjectionUnavailable as error:
                            projection_errors[alias] = error
                            frames.pop(alias, None)
                            continue
                        projections[alias] = projection
                    frames[alias] = (rgb.copy(), None if depth is None else depth.copy())
            if len(frames) == len(captures):
                break

        result: dict[str, dict[str, Any]] = {}
        for alias, camera_id, camera in captures:
            if alias not in frames:
                if alias in projection_errors:
                    raise projection_errors[alias]
                frame = camera.get_current_frame()
                raise RuntimeError(f"camera {alias!r} geometry frame is not ready: {sorted(frame)}")
            rgb, depth = frames[alias]
            rgb = rgb[..., :3]
            if depth is not None and (depth.ndim != 2 or depth.shape != rgb.shape[:2]):
                raise RuntimeError(f"camera {alias!r} depth and RGB grids differ")
            intrinsic = extrinsic = None
            metadata = {"status": "disabled", "reason": "Camera calibration is disabled for this episode"}
            if calibration_enabled:
                intrinsic, metadata = projections[alias]
                extrinsic = native_call(env, lambda native: camera_extrinsic(
                    native, slot=slot, camera_id=camera_id))
            result[alias] = {
                "rgb": np.asarray(rgb, dtype=np.uint8).copy(),
                "depth": None if depth is None else np.asarray(depth, dtype=np.float32).copy(),
                "intrinsic": intrinsic,
                "extrinsic": extrinsic,
                "calibration": metadata,
            }
        return result
    finally:
        for alias, _, camera in captures:
            try:
                if alias in parameters:
                    native_call(env, lambda _: parameters[alias].detach())
            finally:
                try:
                    if depth_enabled:
                        camera.remove_distance_to_image_plane_from_frame()
                finally:
                    camera.pause()


def camera_extrinsic(env, *, slot, camera_id):
    matrix = _camera_to_world(env.camera_manager, slot=slot, camera_id=camera_id)
    # Hosts exposing a room already report room-relative camera poses.
    # Otherwise subtract the selected room origin from the global camera pose.
    if not hasattr(env, "room"):
        scene = getattr(env, "scene", None)
        if scene is None:
            scene = env.scene_manager
        matrix[:3, 3] -= _to_numpy(scene.env_origins[slot])
    return matrix


def _camera_to_world(
    camera_manager: Any,
    *,
    slot: int,
    camera_id: int,
) -> np.ndarray:
    camera = camera_manager.cameras[slot][camera_id]
    getter = getattr(camera, "get_world_pose", None)
    if callable(getter):
        try:
            position, quaternion = getter(camera_axes="ros")
            return _pose_matrix(position, quaternion)
        except TypeError:
            pass

    # USD cameras face local -Z with +Y up; projection uses ROS optical +Z forward
    # and +Y down. Convert explicitly when older Camera versions lack camera_axes.
    xform = camera_manager.cameras_xform[slot][camera_id]
    position, quaternion = xform.get_world_pose()
    matrix = _pose_matrix(position, quaternion)
    matrix[:3, :3] = matrix[:3, :3] @ _OPTICAL_FROM_USD
    return matrix


def _pose_matrix(position: Any, quaternion: Any) -> np.ndarray:
    position = _to_numpy(position).astype(np.float64).reshape(3)
    quaternion_array = _to_numpy(quaternion).astype(np.float64).reshape(4)
    norm = float(np.linalg.norm(quaternion_array))
    if not np.isfinite(norm) or norm <= 1e-12:
        raise ValueError("invalid camera quaternion")
    w, x, y, z = quaternion_array / norm
    rotation = np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
            [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
            [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
        ],
        dtype=np.float64,
    )
    result = np.eye(4, dtype=np.float64)
    result[:3, :3] = rotation
    result[:3, 3] = position
    return result


def _camera_id(alias: str, actual_names: list[str]) -> int:
    candidates = CAMERA_ALIASES[alias]
    for candidate in candidates:
        if candidate in actual_names:
            return actual_names.index(candidate)
    raise KeyError(f"camera {alias!r} does not exist; available cameras: {actual_names}")


def _normalized_cameras(cameras: list[str]) -> list[str]:
    if not cameras:
        raise ValueError("cameras must contain at least one camera")
    result: list[str] = []
    for camera in cameras:
        if camera not in CAMERA_ALIASES:
            raise ValueError(f"unknown camera {camera!r}; choices: {list(CAMERA_NAMES)}")
        if camera not in result:
            result.append(camera)
    return result


def _to_numpy(value: Any) -> np.ndarray:
    if hasattr(value, "detach"):
        return np.asarray(value.detach().cpu().numpy())
    if hasattr(value, "numpy"):
        return np.asarray(value.numpy())
    if hasattr(value, "cpu"):
        return np.asarray(value.cpu().numpy())
    return np.asarray(value)


def _required_attr(owner: Any, name: str) -> Any:
    value = getattr(owner, name, None)
    if value is None:
        raise RuntimeError(f"RoboDojo env is missing {name}")
    return value
