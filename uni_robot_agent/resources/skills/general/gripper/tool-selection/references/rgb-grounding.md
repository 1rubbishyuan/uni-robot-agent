---
name: rgb-grounding
description: Match one physical feature across calibrated RGB views and validate triangulated coordinates before constructing contact targets.
---

# Locate one feature across RGB views

Use `locate(method="triangulate")` only when exposed. It estimates a world point
from explicit correspondences, not object poses, mask centers or a dense scene.
No range image is used. A segment result can help identify an instance, but
independent mask centers in two views are not necessarily the same physical point.

1. Capture 2-3 cameras together while the feature is stationary. Use one returned
   snapshot_id for all observations; the tool uses its stored K and
   T_world_from_optical, never the live wrist transform. RGB pixels must be
   undistorted and use their published original image grid and corresponding K.
2. Find the SAME material feature in each image: a distinguishable corner,
   texture mark or identifiable edge intersection. Occluding silhouette points
   on a curved object can change physical identity across views. Do not match
   a near edge to a far edge, or average unrelated material into a correspondence.
3. Submit one point using integer [row,col] pixels and distinct camera names.
   Example syntax only; select real coordinates from the current images:

   ```json
   {
     "method": "triangulate",
     "snapshot_id": "<returned snapshot_id>",
     "observations": [
       {"camera": "head", "pixel": [240, 380]},
       {"camera": "left_wrist", "pixel": [240, 320]}
     ]
   }
   ```

4. Inspect both returned markers and geometry.valid/reasons. point_xyz exists
   only when valid. The solver requires ray separation of at least 1 degree,
   positive optical Z in all views, and reprojection error at most 3 pixels
   in each view. These are rejection thresholds, NOT a metric accuracy guarantee.
   Good reprojection does not prove that the features have the same identity.
5. If parallax is insufficient, obtain a genuinely different viewing position
   and capture again; pure rotation in place supplies no translation baseline.
   If reprojection is inconsistent, revisit the correspondence, calibration and
   possible motion. Do not keep whichever estimate best fits a desired grasp.
6. Keep this visible surface point separate from a constructed contact pose.
   It does not reveal thickness, underside, normal or object center. Use the
   general grasp-geometry and contact-calculation guidance for pad placement.

The first version requires all views in the same snapshot. It does not match
features automatically or combine images from before and after a robot move.
Whether capture advances physics depends on the environment. A snapshot alone
does not prove simultaneous hardware sensor exposures. Verify synchronization
and stationarity using the current capture contract.
Refresh affected evidence after slip, release or contact-induced movement.

Use the environment's declared camera capabilities. `triangulate` requires
calibrated views and remains usable without SAM. If calibration is disabled,
this triangulation procedure does not apply; `segment`, when exposed, provides
2D evidence only. Do not assume K or extrinsics to manufacture metric coordinates.
