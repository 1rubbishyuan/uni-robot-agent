---
name: pose-targeting
description: Construct Direct pose targets with the correct position frame and rotation semantics. Use when switching motion tools, changing orientation, or aligning a held object's feature.
---

# Specify the point and orientation that must reach the goal

Choose the physical intent first: the approach direction, finger closing axis
and object feature needed for the next operation. A numerically valid pose
does not certify reachability, clearance or contact.

## Construct an absolute six-dimensional target

`move.targets[].pose = [x,y,z,roll,pitch,yaw]` describes the registered grasp
site in world metres and fixed/extrinsic XYZ Euler radians:
`R = Rz(yaw) @ Ry(pitch) @ Rx(roll)`. All six numbers are required.
Read `control_frame` and `grasp_site_description`; do not add a controller offset.
The site is not automatically the tip or working end of a held object.

State `control_pose` still contains position and a wxyz quaternion. Convert that
quaternion to fixed XYZ Euler angles before copying a measured pose into `move`.
For example, with SciPy in a code tool:

```python
from scipy.spatial.transform import Rotation
w, x, y, z = control_pose["quaternion"]
rpy = Rotation.from_quat([x, y, z, w]).as_euler("xyz").tolist()
pose = [*control_pose["position"], *rpy]
```

To translate without reorienting, retain measured RPY and change XYZ. To rotate
in place, retain measured XYZ and change RPY. To move and reorient together,
change both. These specify a Cartesian target, not particular wrist joints.
The wrist, fingers and held features may sweep arcs around the grasp site.

Profiles set waypoint limits and final arrival tolerances; they add no approach
height. Use the current tool contract for supported recovery behavior. If feedback
distinguishes a requested goal from an adjusted execution endpoint, inspect both
and verify measured arrival against the requested goal. Choose tolerances that
the intended operation permits before submitting move.
Select a pre-grasp offset from observed geometry. Preserve the distinction between
arrival tolerance, object localization uncertainty and contact margin; tightening
arrival cannot fix the wrong target.

## Preserve task constraints and stage order

Prefer an orientation this arm has reached when it suits the current contact,
finger closing direction and route. Do not copy orientations between arms merely
because positions appear symmetric. If IK rejects a target, the agent may choose
a different absolute orientation only when the task and held object allow it;
the tool does not search for or silently substitute one.

One `move` interpolates position and orientation together. It does not mean
"translate, then rotate". When clearance must be established first, move to the
clearance pose while preserving orientation, inspect the completed feedback and
images, then send the reorientation target. Likewise, verify pre-grasp arrival
before a dependent approach or close, and withdraw from an insertion before a
large turn. Combine translation and rotation only when their combined sweep is
appropriate. A two-arm target list coordinates steps; list order is not time order.

## Compute relative intent outside the tool

For a relative change, compute a new absolute orientation: a world-axis rotation
uses `R_goal = R_delta @ R_current`; a tool-local rotation uses
`R_goal = R_current @ R_delta`. Convert the result to fixed XYZ Euler radians.
Adding an angle to an arbitrary Euler component is not a general local-axis turn.
A held object's tip is a different pivot and may require a changed XYZ target.

If a call runs out of steps while making progress, continue toward the same
`feedback.arms[arm].target_pose` when the route is still valid. Do not add the
relative angle again. Euler wraparound and gimbal-lock representations can change
numbers without changing physical orientation; compare rotations, not component
subtraction. Quaternions q and -q are also equivalent. Read
`general/gripper/motion-recovery/SKILL.md` for rejection or lost progress.

## Example: the hand aligns but the object's tip misses

A long held part must enter an opening. Aligning the finger center with that
opening does not align an offset tip. Observe the tip and axis relative to the
hand, then choose the hand pose that places the entering feature appropriately.
After reorientation, update that relationship and check retention. A previous
world-axis offset cannot simply be reused after the object rotates.
Use `general/gripper/held-object-geometry/SKILL.md` for the feature relationship
and `general/gripper/placement-and-engagement/SKILL.md` for actual entry evidence.
If the tip is occluded, obtain useful evidence rather than assuming the hand
target proves alignment. Do not silently tighten tolerance to compensate for
targeting the wrong point.


## Schema example

This illustrates the request shape, not a recommended scene coordinate. Replace
the six pose values with the desired registered site pose from current evidence.

```json
{"profile":"precision","targets":[{"arm":"left","pose":[0.2,-0.1,0.9,0,0,0]}],"max_steps":12}
```

When metric opening calibration is exposed, move targets use `gripper_opening_m`.
Otherwise follow the declared opening parameter and units. Omit opening to preserve
the drive target. Native reviewer prefixes retain their declared convention; do
not convert them to metres unless their schema requires it.
Read the current environment's robot description and published manual for
units, frames and capability limits.

A supplied metric opening changes during the pose motion. Inspect targets_reached
and per-arm opening_reached as well as pose_reached; the pose can arrive first.
Omitting opening retains the complete drive intent and preserves closing preload.
It does not copy the measured gap. Use separate stages when full opening must
precede entry into a constrained region; keep independent work concurrent.
