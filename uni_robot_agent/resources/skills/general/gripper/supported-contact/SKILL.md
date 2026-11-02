---
name: supported-contact
description: Plan supported contact and an assisting hand's adjustment or restraint of a receiver. Use when both objects need control or the working hand holds a load while its destination needs adjustment.
---

# Plan motion at the working contact

Identify the surface that acts on the target. With a held tool, it may be an
edge or head far from the hand. Plan its path and clearance rather than the
wrist path alone. Use `general/gripper/contact-effect/SKILL.md` for probes,
effect diagnosis, event counting and conditions for reusing a stroke.

## Pushing and sweeping

When the requested operation uses surface support, retain that support instead
of spending the episode trying to establish airborne retention. Choose contact
from the observed direction of the required object motion.

Choose the contact point with both translation and rotation in mind: an offset
side push may turn the object. Reduce corrections near alignment using the
response observed in this episode rather than blindly reversing a prior push.

For a working edge acting on several objects, inspect each affected object and
the receiving support. If the edge pushes the receiver away, the old collection
route is invalid even when the objects moved forward. Restore the receiver's
support and revise the edge path while preserving completed placements.

### Control overshoot and check settling

Start with a short push and observe how far the object translates or rotates
relative to the hand motion. Do not assume that the object follows the hand or
stops when the hand stops.

As the object approaches the desired position, shorten the push stroke using
the observed response and leave room for any continued sliding. `max_steps`
limits execution steps; reducing it is not a speed or force command.

After each uncertain push, stop advancing into the object and, when support
permits, disengage without dragging it. Use a short `hold` to advance physics
and inspect its final observation for continued drift. If motion remains
uncertain, repeat bounded waiting and observation before another push;
repeated reads of the same state do not establish settling. If the object is
visibly tipping or approaching an edge, take a feasible protective action
rather than waiting for it to settle into a fall.

Correct from the object's settled pose. After overshoot, reduce the next stroke
or revise the contact point and direction rather than immediately issuing a
large reverse push. Verify the response before batching further contact motions.

## Use an assisting hand to prepare or restrain the receiver

Give the assisting hand a specific role: adjust the receiver's position or
orientation to make the route usable, or restrain unwanted motion during contact.
Use `general/gripper/placement-and-engagement/SKILL.md` to assess access and support.
Two arms need not move simultaneously; a stable, accessible receiver needs no
extra grasp.

For example, while the working hand retains a load in clear space, the free hand
can reposition an allowed movable receiver. First establish a feasible contact
and route for that hand, preserving support, existing contents and both arms'
clearance. Pause dependent placement while the receiver moves; verify its new
opening, pose and support before updating the loaded hand's route. Continue
restraint only if needed, otherwise withdraw the assisting hand clear of entry.

Choose an accessible contact on the receiver's body compatible with the expected
load direction. Preserve the opening, placement region and working hand's exit
corridor, including both forearms. Holding a movable lid or flap does not by
itself restrain the body. Avoid a support contact that tips, deforms or obstructs
the receiver; reposition it or choose another support when necessary.

Establish the assisting contact before the operation that relies on it. A closed
gripper or stationary hand alone does not prove restraint. During a short working
motion, check that the receiver stays stable and the manipulated object advances
relative to it. If both travel together, insertion or containment may not have
progressed. If restraint slips, pause dependent motion and revise the contact or
support before continuing from refreshed geometry.

Maintain needed restraint through object release and the working hand's clear
withdrawal; retreat can still hook the rim and displace the receiver. Once the
object and receiver have stable support and the working hand is clear, remove
the assisting contact along a clear route and inspect the final relationships.
Use `general/gripper/state-verification/SKILL.md` for any disturbed relation.

## Motion constrained by a hinge

Identify the moving panel, pivot region and supported base. Contact an accessible
part of the panel and follow its observed constrained motion in short segments.
Moving the base or pressing a neighboring surface does not establish panel
motion. If the base slides, contact misses or something blocks the path, restore
support or clearance before continuing. Check panel-to-base motion directly.

## Presses and strikes

Establish an approach, contact stroke and disengagement at the actual working
surface. For repeated presses, verify release between cycles. For strikes or
tool strokes, clear the working head before lateral travel so it does not drag
across neighboring targets.

Derive target order and required repetitions from the current instruction.
Apply the contact-effect checks before relying on an uncertain stroke.
