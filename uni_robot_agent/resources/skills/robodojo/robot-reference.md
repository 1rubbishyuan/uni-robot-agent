---
name: robodojo-robot-reference
description: X5 robot frames, geometry, metric jaw openings, camera calibration and measured feedback.
---

# RoboDojo X5 robot reference

Read this reference once when reasoning about robot geometry or control units.
Use the current observation and tool schema for available capabilities. Read
`robot_calibration_path` with your file/code tools when numeric geometry is
needed; `read_skill` reads Markdown, not JSON. Inspect the relevant JSON keys
instead of loading every mesh vertex into the conversation.

## Frames and dimensions

Positions are in slot-local world metres, with the parallel-environment origin
removed from both robot poses and camera extrinsics; quaternions are ordered
w,x,y,z. Each public
`control_pose` targets `x5_grasp_site`, the nominal midpoint between the two
distal inward pad area centroids. It is fixed to link6, not a fingertip, contact
sensor or a point on the held object. Local +X points toward the fingertips;
the fingers close along +/-Y; +Z completes the right-handed frame.

`move` requires the full absolute site pose `[x,y,z,roll,pitch,yaw]`, in
metres and fixed-axis XYZ radians: `R = Rz(yaw) @ Ry(pitch) @ Rx(roll)`.
State and FK keep wxyz quaternions. Convert the measured orientation to XYZ
Euler angles to preserve it, or retain measured XYZ to rotate in place.
Position and orientation interpolate together; ordered stages need separate
calls with completed feedback checks. Orientation is never relaxed automatically.
Tracking errors can still move the actual site. Aligning the site does not
guarantee that the fingers, wrist or held object are clear.

The calibration JSON contains:

- `arms[arm].model_id`: the matching robot asset model.
- `arms[arm].hand`: pad-gap convention, opening model and zero-joint hand bounds.
- `arms[arm].hand.contact_geometry`: grasp-site pad/finger bounds at a declared reference gap, with each finger's `translation_per_gap_m`. This is symmetric nominal geometry, not individual measured finger poses.
- `models[id].asset_sha256`: source asset fingerprints.
- `models[id].joints`: parent/child, joint frame, axis and URDF limits.
- `models[id].grasp_site`: native link6-to-site transform and axis meanings.
- `models[id].collision_geometry`: link-local convex proxies from URDF collision meshes.
- `models[id].pads`: distal contact-pad triangles, centroids and areas in finger-link coordinates.
- `models[id].links_without_collision_geometry`: links absent from that collision representation.

For contact and support-clearance calculations, read
`general/gripper/grasp-geometry/references/contact-calculation.md` and run its
small numeric helpers with the current calibration and observations. A positive
endpoint clearance is not a swept-path or attachment guarantee.

Static geometry is not a current world pose. Use measured joints and
`state.arms[arm].metadata.T_world_from_root` for a world-frame calculation.
Prismatic joint values are metres; revolute values are radians. Transform
link-local geometry through its joint chain before comparing it to world
coordinates. The registered site transform already accounts for its link6
offset; do not apply that offset twice.

The hand bounds are axis-aligned boxes at zero finger-joint displacement,
expressed in the grasp-site frame. At an open configuration, translate each
finger along its declared prismatic axis. A full gap is not the outside width:
add finger thickness when checking whether the hand fits inside a slot.
Collision convex hulls can fill recesses and are not exact contact surfaces.
Use the separate pad geometry for contact reasoning. The file does not provide
a collision-checking or obstacle-avoidance service.

## Gripper inputs and measurements

For calibrated X5 tools, `move` accepts
`gripper_opening_m`: the requested full gap between inward pads, in metres.
For example, 0.04 means 40 mm. Omit it to retain the previous drive target.
`release(targets=[{"arm":"right","opening_m":0.04}])` requests a partial
opening while holding the arm pose. Without `opening_m`, release opens fully.
`grasp` uses the native closing preload. Zero metric opening is a close request,
not a guarantee that the physical pads reach an exact zero gap.

For actor overrides use `gripper_openings_m` keyed by arm. The adapter converts
these tool-level overrides before calling the existing actor machinery. Native
VLA vectors and reviewer edit/eef commands retain their normalized opening
convention; do not convert those vectors to metres.

The X5 fingers translate along opposed axes. Their physical joint limits and
native command scale differ: the native closing drive includes preload beyond
the nominal joint travel. Use the published opening model or tools; do not
estimate a native command as requested gap divided by maximum gap.

The published per-arm fields mean:

| Field | Meaning |
|---|---|
| `metadata.gripper_opening_m` | Signed measured inward-pad separation from actual finger body poses |
| `metadata.gripper_target_opening_m` | Nominal gap for the complete retained drive intent, limited to the reachable range |
| `metadata.gripper_applied_command` | Current normalized native drive target after slew limiting |
| `metadata.gripper_applied_target_opening_m` | Nominal gap for that applied intermediate target |
| `gripper` | Legacy normalized measured joint opening, clipped to 0..1 |
| `gripper_command` | Complete accepted native drive intent retained by subsequent actions, not the intermediate slew target |
| `feedback.opening_requests` | Explicit metric request, effective target and saturation for this Direct call |

`move` applies opening and pose together. For an explicit calibrated opening,
read per-arm `opening_reached`, `actual_opening_m`, `target_opening_m`,
`opening_error_m` and `opening_status`. Position/rotation can arrive before the
opening; the same call holds that pose and continues within its original budget.
`targets_reached` checks pose and explicit measurable openings; null means a
requested target could not be measured, not success. Omitted openings report
`opening_reached=null` and do not require a held object to close to zero.
An opening tolerance of 2 mm and an eight-sample progress window with 0.5 mm
minimum improvement bound this check; these are control thresholds, not grasp
quality or force estimates. `opening_stalled`, `opening_unavailable` and
`max_steps` are incomplete outcomes. Queue order alone does not establish
physical preconditions for the next action. If entry requires full opening,
open during the clear pregrasp stage and check before descending.
`grasp`/`release` retain their requested step budgets and report opening evidence;
a blocked close is not automatically a secure grasp.

Actor tool overrides report their request conversion in `opening_requests`.
Small requests below the reachable geometric minimum keep the native closing
preload; the effective target differs from the request and is reported. Values
above the calibrated maximum are rejected. Actual signed measurements are not
clipped to the nominal geometric range.

A target of zero with a measured 23 mm gap can mean contact has blocked closure.
It does not identify the contacted object or prove a secure grasp. Verify
object motion during a short lift and inspect for slipping. Never copy the
measured opening into a new command merely to preserve the current grip.

For camera calibration and environment coordinates, read
`robodojo/perception-reference.md`. For native motion limits, IK recovery and
feedback counters, read `robodojo/gripper/motion-control/SKILL.md`.
