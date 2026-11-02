---
name: grasp-geometry
description: Select a grasp from visible material, finger access and the object's next use. Use for thin, hollow, crowded or partially obstructed objects.
---

# Choose contact that preserves the next operation

Use `general/gripper/state-verification/SKILL.md` when attachment is unresolved.
This reference selects contact geometry; it does not certify a grasp or prescribe
one approach for every object.

Before closure, choose the contact region, closing direction and contact height
as one geometry, with clearance for both fingers and the next operation. Check
the achieved pose against that region; a requested pose is not contact evidence.

## Match the closing direction to accessible material

- For a thin upright part with both faces exposed, consider a side approach
  that closes across its thickness. Check space for both fingers and exclude
  the holder and neighboring parts from the closing region. This candidate
  does not transfer unchanged to the same part lying flat.
- For a ring, open shape or separated strokes, choose observed solid material.
  A mask center can lie in a hole or between parts. Check the selected instance
  rather than grasping a group mask or the support visible through an opening.
- If contact repeatedly catches a holder or adjacent object, withdraw and
  change the exposed region or closing direction. Deeper descent into the same
  obstruction does not isolate the intended part.

## Fit the approach to local material and clearance

Choose a pregrasp opening that encloses the target with observed finger clearance;
fully open fingers can hit a neighbor before reaching the intended contact.
When descent stalls, distinguish that obstruction from target contact or a
kinematic limit before moving deeper. Change opening only while preserving a
clear path and any current support responsibility.

For thin or partly occluded material, compare the selected contact region with
the visible support surface. A valid mask or geometric sample can belong to a hole,
neighbor, finger or background. If an empty grasp contradicts the estimate,
choose a view or local feature that resolves the contact geometry; do not infer
a universal camera bias or repeat an uninformative scan.

## Choose the grasp for what must remain accessible

Leave the entering end, aperture, supporting face or working edge available
for the next operation. For a transfer, also leave material the other hand can
enclose; use `general/gripper/handover/SKILL.md` for the support-transfer procedure.
Inspect whether exactly the intended instance moved, especially when adjacent
parts can be collected together.

A changed support or grip invalidates the old grasp geometry. Re-localize the
accessible edge after a fall, and inspect finger clearance before retrying.
If a temporary placement is permitted, choose one that preserves a regraspable
region; laying a thin part flat can remove the only accessible edge.

## Compare arrival error with the contact margin

A pose_reached result satisfies the motion tolerance; the remaining error may
still exceed the grasp's contact margin. Read actual_position at the registered
grasp site and compare the actual pad positions with observed solid material.
If the remaining approach is clear, use a local precision move or explicit
tolerance suited to that margin, inspect its actual result, then close.
A coarse profile remains useful in clear approach. If the contact region was
already aligned, tighter tracking will not repair a hole, tapered contact or
obstructed finger; change the relevant grasp geometry.

## Distinguish lift retention from carry retention

A short vertical lift shows the intended object following the hand, but a lateral
carry leaves it on the table while the measured opening decreases. The lift
established retention for that motion only. Opening change plus visible loss
establishes a changed hold; it does not identify excessive or insufficient force.

Inspect the new support and contact region. For a shallow pinch on a sloping or
narrow end, consider accessible opposing material with room for both pads and
for the next operation. Change the region, closing direction or supported route
that the evidence implicates; do not simply repeat the same grip more slowly.
After a changed grasp, use a compatible short lift and initial precision carry
at the retained orientation to distinguish pickup from lateral retention.
If it holds and clearance is established, batch already-known route segments to
the next meaningful checkpoint; do not add a capture or locate after each segment.
Recheck at substantial rotation, receiver contact or support transfer using
`general/gripper/state-verification/SKILL.md`. An intact grip needs no repeated
whole-object measurement; a shifted hold needs refreshed feature geometry via
`general/gripper/held-object-geometry/SKILL.md` before precise placement.

## Recover from the stage that actually failed

Distinguish an empty closure, loss during initial lift, and loss during carry.
For an empty closure, resolve pad/material overlap and actual arrival first.
For lift loss, inspect opposing contact and changed support; for carry loss,
inspect load orientation, clearance and the hand-to-object relation. Opening
alone does not distinguish these outcomes. Keep unobserved mechanical causes
as hypotheses and change a factor supported by the observed failure.

When local shape leaves several plausible contacts, read
`general/gripper/grasp-geometry/references/shape-based-grasping.md` for conditional
candidates based on cross-section, curvature, openings, branches and opposing
faces. Select from current finger access and support clearance; a shape category
does not establish a successful grasp.

When the current tools expose metric jaw calibration, use `gripper_opening_m`
for move or `release` with target `opening_m` to pre-open. Otherwise follow the
declared opening units. Include finger thickness when judging clearance; consult
published hand geometry through `robot_calibration_path` when available.
Read the current environment's robot description and published manual for
units, frames and capability limits.

For a staged grasp, first reach a geometry-derived pre-grasp pose and inspect
completed feedback, then approach and verify before closing. `move` adds no
pre-grasp offset. Its position/orientation errors remain available for each
arm; pose arrival alone does not prove contact or attachment.

When contact height, finger clearance or pad overlap is uncertain, read
`general/gripper/grasp-geometry/references/contact-calculation.md`. Use file/code
tools to compute from the published hand geometry and valid 3D locate points
(from exposed pixels or triangulate methods, not a 2D-only segment);
print the candidate pose, gap, clearance and uncertainty before selecting it.
Do not infer unseen thickness or invent a fixed grasp-height correction.
Opening can change during move. If material must fit between the fingers before
entry, open in the clear pregrasp segment and inspect opening_reached before
approaching; endpoint completion does not prove intermediate clearance.
