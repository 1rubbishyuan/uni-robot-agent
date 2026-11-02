---
name: robodojo-perception-reference
description: RoboDojo slot-local coordinates, camera calibration, pixel conventions and RGB/depth capabilities.
---

# Use RoboDojo observation coordinates

Positions are in slot-local world metres. The parallel-environment origin is
already removed from robot poses and camera extrinsics; do not subtract it again.
RoboDojo capture does not advance physics. Ordinary RGB records can lack valid
calibration; when has_calibration=true, use capture_scene for same-frame geometry.
When has_calibration=false, calibration instructions below do not apply: capture
returns RGB without K or extrinsics, and repeated capture cannot enable geometry.

## Cameras and pixels

Observation and action-image outputs expose `camera_calibration[camera]`:
`intrinsic_K`, `T_world_from_optical`, `image_size=[height,width]`, and
`image_path`. When calibration is valid, these describe the original local image file, not an arbitrary
resized screenshot. Ordinary RGB/action records may report unavailable calibration
with null matrices; use `capture_scene` for same-frame projection calibration.
Never substitute an older matrix or assume identity when calibration is unavailable. Optical +X is right, +Y down and +Z forward.

For a world point, compute its optical coordinates with the inverse extrinsic,
then multiply by K and divide by optical Z to get `(u,v)=(column,row)`. Points
behind the camera cannot be projected as visible points. `locate` arguments
instead use `[row,col]`; swap the order deliberately.

For a pixel, inverse K gives a ray, not an absolute 3D point. K and extrinsics
alone do not determine distance along that ray. A known plane, another view or
a range measurement is needed for a unique position. Use the locate methods
actually exposed. `triangulate` estimates one world point from explicit matching
pixels in 2-3 cameras of one snapshot; it uses K and optical-to-world transforms,
not a range image. Inspect geometry.valid and reasons; geometry.point_xyz exists
only for a valid result. Correspondence identity remains your responsibility.
Read `general/gripper/tool-selection/references/rgb-grounding.md` for matching
and uncertainty checks. Pinhole inputs must be undistorted with the matching K.

`view_env_state` reads immutable historical RGB records.
The wrist camera extrinsic changes with the arm; use the matrix from the same
snapshot as the image. Missing calibration in an old record is unknown, not
identity. `snapshot_id` and `image_state_version` identify image evidence;
current action state can be newer than the last published image.
`captured_at` is snapshot acquisition time, not a per-sensor exposure timestamp.

## Pixel-center convention

Image pixels use zero-based integer `[row,col]` centers. Published K already
includes the renderer-to-pixel-center conversion; do not add a half-pixel or
replace fy with fx. Use K from the same snapshot and image grid. If ordinary RGB
reports unavailable calibration, use `capture_scene` and `locate` for geometry.

## Camera capabilities

Environment operators can use `--no-depth` with calibration retained, or add
`--no-camera-calibration` for pure RGB. Use declared camera capabilities and
exposed methods. `triangulate` needs calibrated views but not SAM. With depth
or calibration disabled, `segment` returns only 2D evidence; without calibration
there is no pixels/triangulate method or plane fitting.

Use `general/gripper/tool-selection/SKILL.md` for choosing evidence, and its
`general/gripper/tool-selection/references/rgb-grounding.md` and
`general/gripper/tool-selection/references/depth-grounding.md` references for
feature matching and surface ownership.
