---
name: contact-calculation
description: Calculate candidate pad contact and finger clearance from published hand geometry, valid surface points and measured poses.
---

# Compute a candidate before choosing contact

Use this reference when a small contact region or support clearance determines
whether a candidate grasp is feasible. It is a general geometry calculation,
not an object recipe or an attachment detector. Reuse adequate measurements.

## Required geometry capabilities

Use these helpers only when the environment publishes the named hand-geometry
schema and permits reading it with file/code tools. The calculation assumes
parallel opposed fingers, closing along local Y with pad overlap in X/Z, and the
published translation-per-gap model. Verify those assumptions from the current
robot description. Other hand models need their own geometry calculation; do not
invent missing calibration, measured gaps or force information.

## Inputs from the existing tools

- Read `robot_calibration_path` returned by scene tools using file/code tools.
  Parse `arms[arm].hand.contact_geometry`; do not load the full meshes into chat.
  `read_skill` reads Markdown, not JSON or Python scripts.
- Use calibrated `capture_scene` and a supported `locate` method for missing
  points. `triangulate` supplies geometry.point_xyz only when geometry.valid;
  provide the same visible feature in 2-3 cameras, using [row,col] pixels.
  If `pixels` is exposed, associate its valid geometry.points_xyz with
  geometry.input_pixels. Keep different surfaces in separate measurements.
  A 2D segment without geometry is not a numeric contact position.
- Use the actual `state.arms[arm].control_pose` and measured
  `metadata.gripper_opening_m` to inspect the present hand. For a candidate,
  choose a full world pose6 and a physically reachable gap explicitly.
- Establish the support plane and visible material from observations. A single
  surface point does not determine thickness, underside, normal or object pose.
  If a needed dimension is unknown, identify it before computing a verdict.

Use the published pixel convention and K from the same snapshot and image grid.
Preserve separate fx and fy values. If ordinary RGB reports unavailable calibration,
use calibrated capture and localization tools before computing contact geometry.

Use the declared world frame consistently for poses, surface points and support.
Apply a frame transform only when the published frames actually differ.
The site transform is already included in control_pose; do not add it again.
move uses `[x,y,z,roll,pitch,yaw]` with `R=Rz@Ry@Rx`. State quaternion order is
wxyz. The following pure calculations use numpy; they do not execute actions.

```python
import json
from itertools import product
import numpy as np


def read_hand(calibration_path, arm):
    with open(calibration_path) as stream:
        return json.load(stream)["arms"][arm]["hand"]["contact_geometry"]


def pose6_rotation(pose6):
    x, y, z, roll, pitch, yaw = map(float, pose6)
    cr, sr = np.cos(roll), np.sin(roll)
    cp, sp = np.cos(pitch), np.sin(pitch)
    cy, sy = np.cos(yaw), np.sin(yaw)
    rx = np.array([[1, 0, 0], [0, cr, -sr], [0, sr, cr]])
    ry = np.array([[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]])
    rz = np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]])
    return np.array([x, y, z]), rz @ ry @ rx


def measured_pose(control_pose):
    q = np.asarray(control_pose["quaternion"], dtype=float)
    w, x, y, z = q / np.linalg.norm(q)
    rotation = np.array([
        [1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
        [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
        [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)],
    ])
    return np.asarray(control_pose["position"]), rotation


def finger_points(hand, gap_m, key):
    result = {}
    for name, finger in hand["fingers"].items():
        bounds = np.asarray(finger[key])
        corners = np.array(list(product(*bounds.T)))
        shift = (gap_m - hand["reference_opening_m"]) * np.asarray(finger["translation_per_gap_m"])
        result[name] = corners + shift
    return result


def inspect_candidate(hand, position, rotation, gap_m, material_points_world,
                      support_point, support_normal, uncertainty_m):
    # The normal points out of the support, toward free space.
    normal = np.asarray(support_normal, dtype=float)
    normal /= np.linalg.norm(normal)
    material = (np.asarray(material_points_world) - position) @ rotation
    low, high = material.min(axis=0), material.max(axis=0)
    pads = finger_points(hand, gap_m, "pad_bounds_m")
    fingers = finger_points(hand, gap_m, "finger_bounds_m")
    result = {}
    for name in fingers:
        world = fingers[name] @ rotation.T + position
        clearance = float(((world - support_point) @ normal).min())
        pad_low, pad_high = pads[name].min(axis=0), pads[name].max(axis=0)
        overlap = np.minimum(high, pad_high) - np.maximum(low, pad_low)
        result[name] = {
            "support_clearance_m": clearance,
            "clearance_after_uncertainty_m": clearance - uncertainty_m,
            "sampled_material_overlap_xz_m": overlap[[0, 2]].tolist(),
        }
    result["sampled_material_y_range_m"] = [float(low[1]), float(high[1])]
    result["gap_m"] = float(gap_m)
    return result
```

Call these helpers with observed inputs and print the result alongside the
candidate pose and snapshot/state versions. Do not copy missing observations
from a hypothetical example. Convert returned numpy values to lists if needed
when storing a record. Missing/nonfinite inputs or an invalid support normal
must be resolved before interpreting a result.

## Interpret the calculation

Finger bounds are conservative boxes. Their signed distance to the support is
a clearance lower bound at this pose; a negative value rejects or questions the
candidate but can overestimate interference. Check the wrist/forearm, neighbors
and intermediate poses separately. Endpoint clearance is not path clearance.

The X/Z overlap compares the selected material's bounding range with the pad
range; positive overlap is only a candidate screen. It does not establish solid
material in a hole, opposing contact, identity, friction or attachment. Inspect
the actual selected material and the Y closing interval. Sparse surface points
may not describe the required extent; do not label their bounding range as the
whole object's dimensions. Include localization, pose and orientation error in
uncertainty_m; orientation error grows into position error away from the site.

The hand summary assumes equal opposed finger motion. A measured full gap
alone cannot recover asymmetric blocked finger positions. Treat asymmetry as
uncertainty, not as a precisely reconstructed contact. Use current images to
resolve it. Never replace closing preload with the measured gap merely to hold.

Choose a candidate only when contact material and clearance have adequate
margin. Use its pose6 and gripper_opening_m with move. If full opening is needed
before entering material, open during the clear pregrasp stage and inspect
opening_reached before descent. Compare the final actual pose/gap with the
candidate; a numeric feasibility screen is not evidence that pickup succeeded.
