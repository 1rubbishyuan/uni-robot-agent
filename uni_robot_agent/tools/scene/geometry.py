"""Pinhole projection and geometric evidence over frozen, metric camera frames."""

from __future__ import annotations

import numpy as np

from uni_robot_agent.services.environment.snapshots import CameraFrame


def project_pixels(frame: CameraFrame, pixels) -> dict:
    depth, intrinsic, extrinsic = _calibration(frame)
    points = _pixels(pixels, depth.shape)
    world, valid = _world_points(depth, intrinsic, extrinsic, points)
    if not valid.any():
        raise ValueError("Selected pixels have no valid depth")
    selected = world[valid]
    return {
        "image_size": list(depth.shape), "input_count": len(points),
        "valid_count": int(valid.sum()), "pixels": points[valid].tolist(),
        "input_pixels": points.tolist(), "valid": valid.tolist(),
        "points_xyz": _rounded(selected), **point_statistics(selected),
    }


def project_mask(frame: CameraFrame, mask) -> dict:
    depth, intrinsic, extrinsic = _calibration(frame)
    mask = np.asarray(mask, dtype=bool)
    if mask.shape != depth.shape:
        raise ValueError("Mask must match the source frame; resize with an explicit pixel mapping")
    pixels = np.argwhere(mask)
    if not len(pixels):
        raise ValueError("Segmentation mask is empty")
    world, valid = _world_points(depth, intrinsic, extrinsic, pixels)
    if not valid.any():
        raise ValueError("Segmented region has no valid depth")
    return {
        "image_size": list(depth.shape), "mask_pixels": len(pixels),
        "valid_count": int(valid.sum()), **point_statistics(world[valid]),
    }


def point_statistics(points) -> dict:
    points = np.asarray(points, dtype=np.float64)
    if points.ndim != 2 or points.shape[1] != 3 or not len(points):
        raise ValueError("Expected a nonempty [N,3] point array")
    if not np.isfinite(points).all():
        raise ValueError("Point statistics require finite coordinates")
    median = np.median(points, axis=0)
    return {
        "median_xyz": _rounded(median),
        "bounds_xyz": {
            "low": _rounded(np.percentile(points, 5, axis=0)),
            "high": _rounded(np.percentile(points, 95, axis=0)),
        },
        "z_percentiles": {
            "low": round(float(np.percentile(points[:, 2], 10)), 5),
            "median": round(float(median[2]), 5),
            "high": round(float(np.percentile(points[:, 2], 90)), 5),
        },
        "spread_m": round(float(np.median(np.linalg.norm(points - median, axis=1))), 5),
    }


def mask_geometry_quality(geometry: dict, *, workspace_bounds=None) -> dict:
    height, width = geometry["image_size"]
    count = geometry["mask_pixels"]
    fraction = count / (height * width)
    valid_ratio = geometry["valid_count"] / count if count else 0.0
    spans = np.asarray(geometry["bounds_xyz"]["high"]) - geometry["bounds_xyz"]["low"]
    reasons = []
    if geometry["valid_count"] < 32 or valid_ratio < 0.5:
        reasons.append("insufficient_valid_depth")
    if fraction > 0.35:
        reasons.append("mask_covers_too_much_image")
    if spans[0] > 0.8 or spans[1] > 0.8:
        reasons.append("horizontal_bounds_too_large")
    if spans[2] > 0.5:
        reasons.append("vertical_bounds_too_large")
    if geometry["spread_m"] > 0.3:
        reasons.append("world_points_too_spread_out")
    if workspace_bounds is not None:
        low, high = np.asarray(workspace_bounds, dtype=np.float64)
        median = np.asarray(geometry["median_xyz"])
        if ((median < low) | (median > high)).any():
            reasons.append("median_outside_workspace")
    return {
        "valid": not reasons, "reasons": reasons,
        "mask_fraction": fraction, "valid_depth_ratio": valid_ratio,
        "spans_xyz_m": _rounded(spans),
    }


def _calibration(frame: CameraFrame):
    if frame.depth_m is None or frame.intrinsic_K is None or frame.T_world_from_optical is None:
        raise ValueError("This snapshot has no aligned depth and camera calibration")
    depth = np.asarray(frame.depth_m, dtype=np.float64)
    intrinsic = np.asarray(frame.intrinsic_K, dtype=np.float64)
    extrinsic = np.asarray(frame.T_world_from_optical, dtype=np.float64)
    if intrinsic[0, 0] <= 0 or intrinsic[1, 1] <= 0:
        raise ValueError("Camera focal lengths must be positive")
    return depth, intrinsic, extrinsic


def _pixels(values, shape):
    points = np.asarray(values)
    if points.ndim != 2 or points.shape[1] != 2 or not len(points):
        raise ValueError("Pixels must be a nonempty list of [row, col] pairs")
    if not np.issubdtype(points.dtype, np.number) or not np.isfinite(points).all():
        raise ValueError("Pixel coordinates must be finite integers")
    integers = points.astype(np.int64)
    if not np.array_equal(points, integers):
        raise ValueError("Pixel coordinates must be integers")
    if (integers < 0).any() or (integers >= np.asarray(shape)).any():
        raise ValueError("Pixel lies outside the source frame")
    return integers


def _world_points(depth, intrinsic, extrinsic, pixels):
    rows, cols = pixels.T
    z = depth[rows, cols]
    valid = np.isfinite(z) & (z > 0)
    # Avoid invalid-depth arithmetic; validity remains explicit for every requested pixel.
    z = np.where(valid, z, 0.0)
    x = (cols - intrinsic[0, 2]) * z / intrinsic[0, 0]
    y = (rows - intrinsic[1, 2]) * z / intrinsic[1, 1]
    optical = np.stack([x, y, z, np.ones_like(z)], axis=-1)
    world = (optical @ extrinsic.T)[:, :3]
    return world, valid & np.isfinite(world).all(axis=1)


def _rounded(value):
    return np.round(np.asarray(value, dtype=np.float64), 5).tolist()


def fit_plane(points):
    xyz = np.asarray(points, dtype=float)
    if xyz.ndim != 2 or xyz.shape[1] != 3 or len(xyz) < 3 or not np.isfinite(xyz).all():
        raise ValueError('Provide at least three finite base-frame points')
    center = xyz.mean(axis=0)
    _, singular, vectors = np.linalg.svd(xyz-center, full_matrices=False)
    if singular[1] < .01:
        raise ValueError('Table points need non-collinear spatial coverage')
    normal = vectors[-1]
    if normal[2] < 0:
        normal = -normal
    offset = -float(normal@center)
    return dict(normal=normal.tolist(), offset_m=offset,
                residual_rms_m=float(np.sqrt(np.mean((xyz@normal+offset)**2))),
                point_count=len(xyz), method='fit_corresponding_table_surface_points')
