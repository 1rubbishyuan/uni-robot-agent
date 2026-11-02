---
name: placement-and-engagement
description: Plan and verify support, containment, nesting or mechanical engagement from visible object features. Use when receiver stability, access or relative geometry determines the requested relationship.
---

# Verify the requested relationship between objects

Use `general/SKILL.md` for acquisition, transport and withdrawal. Identify the
required support, containment, engagement or alignment. Use
`general/gripper/state-verification/SKILL.md` for unresolved relationship evidence
and `general/gripper/contact-effect/SKILL.md` to diagnose an entry probe.

## Preserve reference features and access

For a goal defined by an initial arrangement, record the destination references
before moving those objects. Keep that reference separate from current contact
coordinates. Distinguish position, orientation and support requirements; do not
add a preferred orientation when the instruction leaves it unconstrained.

Choose visible features that distinguish the required orientation. An undirected
long axis cannot distinguish opposite ends of an asymmetric part. Image slope
alone is not world yaw; use calibrated geometry when numerical correction is
needed. Inspect the object after reorientation because it can shift in the grip.

## Preserve the receiver through acquisition and transport

Before acquiring the next object, confirm its receiver will remain stable,
reachable and accessible through the transfer. Choose from observed behavior
and the expected contact, rather than assuming every container needs two hands:

- Stable receiver and clear entry: place directly using its existing support.
- Sliding, rotation or tipping during contact, or expected contact that requires
  restraint: consider an available assisting hand or another stable support.
- Blocked opening or inaccessible interior: revise the approach, clear an
  obstructing lid when permitted, or reposition the receiver before loading it.
  If the working hand already holds a load, assess whether the free hand can
  prepare the receiver while preserving that hold. Read
  `general/gripper/supported-contact/SKILL.md` for adjustment or restraint.
  Holding an inaccessible receiver still does not make the route feasible.

Assign the assisting hand's role before pickup and preserve its existing support
responsibilities. Do not park it away or leave the receiver on moving support
without a viable plan. Use `general/gripper/supported-contact/SKILL.md` to establish
restraint without blocking entry; simultaneous motion of both hands is optional.

If the receiver moves, pause motion that depends on its old geometry. Recheck
its opening, floor, orientation and affected contents, then update the entry and
placement route. A held object's accurate world position does not establish its
position inside a displaced receiver. Restore a usable destination before
continuing. Use `general/gripper/handover/SKILL.md` for hand-to-hand support transfer and
`general/gripper/held-object-geometry/SKILL.md` for the load's actual clearance.

## Build support and containment

- Keep a stable, reachable base when it already serves the next placement.
  For a spanning part, establish its supports before adding that load. After
  release, check that it actually rests on those supports rather than leaning
  against them or remaining supported by the hand.
- For nesting, align the actual receiving opening and the held part's rim or
  axis. Inspect tilt and edge contact; overlapping silhouettes do not establish
  that one part seated inside the other.
- For containment, check the whole object's position after withdrawal. A part
  can bridge a rim, protrude through an opening or be drawn back out by a finger.
  Leave room for subsequent entries and withdrawal before committing placement.

## Establish engagement before advancing

Identify the entering feature and the receiving aperture, including their axes
or planes. Keep the grasp clear of those features and the receiver stable.
Choose alignment precision from their actual clearance. When observed alignment
supports a bounded entry probe, test entry instead of repeatedly refining the
same coordinates. If an edge catches or motion stalls, retract clear before
correcting the mismatch; do not rotate while wedged or push deeper unchanged.

For turning after entry, preserve axis alignment, grip and receiver support.
Use controlled segments and inspect relative part motion; grasp-site rotation alone
does not establish advancement. If regrasping is needed, preserve the assembly's
support first. A rotation about the grasp site can sweep an offset contact feature;
use the held-object geometry and motion-recovery references when this matters.

## Continue engagement until the required relation is established

After initial engagement, choose an observable relative progress measure and an
end condition appropriate to the goal. Depending on the mechanism, useful
evidence includes remaining gap, exposed length, relative angle, seating surfaces
or a visible latch state. Initial entry is not full seating; resistance alone
can mean an edge catch, obstruction or misalignment rather than the endpoint.

Choose contact checkpoints by a possible change in engagement, support, progress
or proximity to the endpoint. A small commanded increment alone does not require
remeasuring all geometry. At the checkpoint, inspect relative progress and the
end condition using existing images first; obtain new evidence only where needed.

- Engagement maintained, progress observed, endpoint not reached: continue the
  effective operation in controlled segments while support and clearance hold.
- Engagement lost: restore alignment and control before further advancement.
- No progress: diagnose offset, tilt, slipping grip, obstruction or moving
  receiver; change the supported hypothesis instead of repeating more force.
- Endpoint supported by evidence: release when support permits, withdraw clear
  and verify the required relation without disturbing it.

Hand travel or resistance alone does not prove seating, tightening or locking.
For example, while engagement holds and the gap visibly narrows, continue a
bounded segment without resegmenting both parts. Shorten it near seating or a
changed response; check an ambiguous endpoint. Reuse approaches only where
the new parts' geometry is compatible. Do not infer unobserved torque or locking.

When withdrawal is required before reorientation, use separate `move` calls and
verify withdrawal before turning. A single pose target starts translation and
rotation together. Free-space transport may combine them when the observed
clearance and load permit it.
