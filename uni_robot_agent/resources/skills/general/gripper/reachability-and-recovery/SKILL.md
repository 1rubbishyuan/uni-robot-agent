---
name: reachability-and-recovery
description: Choose an arm, approach and permitted relay for the complete gripper route. Use before committing to a grasp whose destination is uncertain or when a route needs reassignment.
---

# Choose a feasible route through the whole operation

Consider source access, loaded transport, destination contact and empty-hand
withdrawal together. Reachability depends on orientation, height, current arm
configuration and the chosen position frame, not a fixed left/right coordinate
boundary. A failed pose is evidence about that attempt, not the whole workspace.

## Resolve consequential uncertainty before pickup

Reuse full poses this arm actually reached when they remain compatible with the
scene. When destination access is doubtful, a clear empty-hand approach can test
it before committing to acquisition. This is useful evidence, not a requirement
for every pickup or proof that the same route clears a held object.

Select task-compatible orientations and staging poses with room for the load,
fingers and forearms. If the instruction leaves assignments or order open,
choose them to reduce difficult transfers; preserve explicitly required pairings,
destinations and sequences. Assign each hand's support role before moving it.

## Change the route without losing useful support

When a single-arm route repeatedly fails, reassess hand assignments before
another acquisition or carry attempt. Compare changing the carrying arm, using
the free hand to adjust the receiver, and a permitted supported relay. Choose
from observed access, support and clearance; an untested arm is not guaranteed
to reach. Identify what changes, resolve queued work, and test the doubtful
part of the revised route before repeating the full pickup-and-carry sequence.

Use `general/gripper/supported-contact/SKILL.md` for permitted receiver adjustment
or restraint and `general/gripper/handover/SKILL.md` for hand-to-hand transfer.

## Case: pickup is reachable, but a vertical basket entry is rejected

A grasp succeeds near the arm, but a straight fingers-down carry cannot enter the
basket. In a successful recovery, the arm withdrew to clear space, changed to a
previously reached carrying inclination, then entered along short precision
segments. Reuse the reasoning, not that episode's coordinates or quaternion:
compare the required object pose, grasp-site orientation, full-load clearance and
withdrawal path. A tilted hand may enter where the vertical one could not, but
an orientation required by the task must still be satisfied at placement.
When destination access is doubtful before pickup, test its consequential part
with an empty hand if useful. Reuse an established compatible route; do not
repeat reach probes for every object or treat an empty-hand pass as load clearance.

## Case: source and destination need different hands

One arm reaches the source but lacks a demonstrated destination route; the other
has destination access. Before acquisition, compare direct transport with a
permitted supported relay that both hands can approach and withdraw from. Choose
a stable staging pose that exposes a receiving grasp; laying a thin part flat can
hide that region. Confirm receiver access before releasing donor support, then
localize affected geometry and verify the receiving grasp before transport.
A supported relay does not fulfill an explicit hand-to-hand instruction. If the
category-to-container assignment is free, choose it using demonstrated access;
if destinations or order are specified, preserve them and plan the transfer.
Do not infer permanent left/right workspace boundaries from one rejected pose.

For IK rejection, stalls, joint jumps or partial execution, read
`general/gripper/motion-recovery/SKILL.md`. It owns tool-frame semantics and
motion recovery. Resolve queued work and inspect the actual state before route
reassignment. Test a meaningful changed approach and confirm progress before
batching dependent work; repeated zero-step rejection cannot be repaired by
increasing the step budget or assuming the requested pose was reached.

When the task permits choosing a shared destination, choose it for every
required approach, loaded placement and withdrawal, not just the first pickup.
A successful placement with no clear withdrawal route is not a reusable route.
If a changed destination is permitted and necessary, preserve the specified
object relationships and reassess the affected arms before moving the load.
Do not abandon a required operation as physically impossible on the evidence
of repeated failures with one contact geometry or one arm configuration.
