---
name: held-object-geometry
description: Locate the bottom, entering end or working feature of a held object relative to the hand. Use when clearance, placement or tool contact depends on the load's actual pose.
---

# Plan with the held object's actual features

Identify the feature needed for the next operation: the lowest edge for clearance,
an entering end and axis for engagement, or a working surface for tool contact.
Estimate its relation to the measured hand from a compatible observation. The
finger center, mask median and object center are not interchangeable features.
Use `general/gripper/grasp-geometry/SKILL.md` to keep the needed feature accessible.

## Preserve only geometry that remains valid

A held object can hang tilted or offset even when the hand reaches its requested
pose. Inspect its actual bottom, rim, long extent or working end rather than
assuming the initial grasp centered it. After reorientation, transform the
feature relationship with the new orientation and check that the grip did not
shift; a world-axis offset cannot simply be reused after rotation.

Slip, regrasp or support transfer invalidates the old hand-to-object relationship.
Refresh the affected feature before precise contact. Reuse a confirmed relation
through undisturbed motion; do not repeatedly measure the entire object.

## Check both the moving shape and the receiver

Plan approach and withdrawal for the whole load and fingers, including edges
below or beside the grasp. A hand above a rim does not establish clearance for
the lowest corner. Localize the receiving opening or support again if it moved;
an accurate held-object offset cannot compensate for a stale destination.

Separate hand tracking, retention and effective contact. A working end following
the hand establishes control of the tool, not its effect on the target. Use
`general/gripper/contact-effect/SKILL.md` for that effect and
`general/gripper/placement-and-engagement/SKILL.md` for the resulting relation.
When fingers obscure the deciding feature, choose a useful view or a supported
release and withdrawal if the operation permits it; an occluded mask can appear
smaller or lower without the object having seated.

## Case: the object is already aligned, but the hand looks rotated

The object's identifiable face and directed features already satisfy the required
world orientation, although the grasp-site yaw is nonzero. Preserve that useful object
orientation during transport when the route permits it. Zero grasp-site yaw is not an
object-alignment objective; an unnecessary loaded turn can shift the grip and
make the remaining route harder. Confirm alignment from distinguishing features,
not bbox aspect ratio alone. If rotation is required, choose it from the measured
hand-to-object relationship and available clearance, not an assumed centered grip.
After slip, regrasp or an unexpected turn, refresh the affected relationship before
using the old placement offset. When retention and geometry remain valid, reuse
them and inspect the released result without repeating localization at each move.

Before precise engagement, compare the actual working feature and its axis
with the receiver, then account for the fingers' approach and withdrawal space.
Do not translate a receiver coordinate directly into a gripper-center target
without the supported hand-to-feature relationship. A tighter motion tolerance
cannot repair an incorrect feature offset or stale held orientation.
