"""Metric triangulation of explicit corresponding pixels in calibrated RGB views."""
from itertools import combinations

import numpy as np

from uni_robot_agent.tools.scene.geometry import _pixels

MIN_RAY_ANGLE_DEG = 1.0
MAX_REPROJECTION_ERROR_PX = 3.0


def triangulate(frames, pixels):
    """Use optical-to-world transforms; never inspect depth or infer correspondences.

    Callers bind all views to one immutable snapshot. Inputs must be undistorted
    pinhole images. Geometry checks cannot establish semantic point identity.
    """
    if len(frames) != len(pixels) or not 2 <= len(frames) <= 3:
        raise ValueError("Triangulation requires 2 or 3 corresponding views")
    origins, rays, calibrations, selected = [], [], [], []
    for frame, pixel in zip(frames, pixels):
        uv = _pixels([pixel], frame.rgb.shape[:2])[0][::-1].astype(float)
        if frame.intrinsic_K is None or frame.T_world_from_optical is None:
            raise ValueError("Triangulation requires snapshot K and optical-to-world calibration")
        k, t = np.asarray(frame.intrinsic_K, dtype=float), np.asarray(frame.T_world_from_optical, dtype=float)
        if (k.shape != (3, 3) or t.shape != (4, 4)
                or not np.isfinite(k).all() or not np.isfinite(t).all()
                or k[0, 0] <= 0 or k[1, 1] <= 0 or abs(np.linalg.det(k)) < 1e-12
                or not np.allclose(k[2], [0, 0, 1], atol=1e-6)
                or not np.allclose(t[3], [0, 0, 0, 1], atol=1e-6)
                or not np.allclose(t[:3, :3].T @ t[:3, :3], np.eye(3), atol=1e-5)
                or not np.isclose(np.linalg.det(t[:3, :3]), 1, atol=1e-5)):
            raise ValueError("Invalid pinhole K or rigid optical-to-world transform")
        if frame.calibration and frame.calibration.get("status") not in (None, "valid"):
            raise ValueError("Snapshot camera calibration is not valid")
        ray = t[:3, :3] @ np.linalg.solve(k, np.r_[uv, 1.])
        rays.append(ray / np.linalg.norm(ray))
        origins.append(t[:3, 3])
        calibrations.append((k, t))
        selected.append(uv)
    # A useful baseline must separate rays, not merely rotate a camera in place.
    angle = max(np.degrees(np.arccos(np.clip(abs(a @ b), 0, 1)))
                for a, b in combinations(rays, 2))
    result = {"valid": False, "reasons": [], "ray_angle_deg": float(angle),
              "min_ray_angle_deg": MIN_RAY_ANGLE_DEG,
              "max_allowed_reprojection_error_px": MAX_REPROJECTION_ERROR_PX}
    if angle < MIN_RAY_ANGLE_DEG:
        return dict(result, reasons=["insufficient_parallax"])
    projectors = np.array([np.eye(3) - np.outer(ray, ray) for ray in rays])
    point = np.linalg.solve(projectors.sum(axis=0), np.einsum("nij,nj->i", projectors, origins))
    if not np.isfinite(point).all():
        return dict(result, reasons=["nonfinite_solution"])
    errors, in_front = [], []
    for (k, t), uv in zip(calibrations, selected):
        optical = t[:3, :3].T @ (point - t[:3, 3])
        in_front.append(bool(optical[2] > 1e-6))
        if not in_front[-1]:
            errors.append(None)
            continue
        projected = k @ optical
        error = float(np.linalg.norm(projected[:2] / projected[2] - uv))
        errors.append(error if np.isfinite(error) else None)
    result.update(reprojection_errors_px=errors, in_front=in_front)
    if not all(in_front):
        result["reasons"].append("not_in_front_of_all_cameras")
    if any(e is None or e > MAX_REPROJECTION_ERROR_PX for e in errors):
        result["reasons"].append("inconsistent_reprojection")
    result["valid"] = not result["reasons"]
    if result["valid"]:
        result["point_xyz"] = point.tolist()
    return result
