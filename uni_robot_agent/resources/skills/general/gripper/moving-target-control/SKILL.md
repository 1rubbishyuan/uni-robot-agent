---
name: moving-target-control
description: Capture or interact with a moving physical target using fresh geometry and observed motion. Use when the target or its receiver can move during approach, closure or transfer.
---

# Plan contact against motion, then verify capture

Use `general/gripper/scene-memory/SKILL.md` to preserve identity and reference
images. Recognizing an object does not establish its current contact position.

## Prepare a reachable contact window

Compare observations with their capture time or native step to establish motion
direction and a local speed estimate. Use actual environment advancement: in a
step-driven scene, reasoning time alone need not move the target; in a continuously
advancing scene, account for elapsed time and observation latency. Do not mix
these clocks or assume constant speed after a disturbance.

Choose a reachable contact region and prepare a compatible orientation before
the target arrives when useful. Allow for movement during the executed approach
and closure, not only the displacement between images. Check finger clearance,
solid contact material and relative drift through the closing region. Refresh
geometry near final contact; shorten the segment whose accuracy depends on an
old moving-target position instead of queuing a long stale chase.

Before capture, preserve the receiver and the subsequent transfer route. A
container on moving support may leave reach while the other hand pursues the
target. Use `general/gripper/placement-and-engagement/SKILL.md` for support roles.

## Distinguish a miss, capture and later loss

Inspect the completed closure and, when lifting is appropriate, a short lift.
Compare the target's original support with its motion relative to the hand;
centered wrist imagery or blocked fingers alone do not establish capture.
Use `general/gripper/state-verification/SKILL.md` for unresolved attachment.

After a miss, update identity, current position and reachable contact window.
After slip, reconsider grip geometry and the loaded motion as well as timing;
one successful lift does not establish retention for the whole transfer.
If the receiver moved or was lost, revise the dependent placement plan before
continuing the carry. Wait for a return only when observations or the instruction
support recurrence; do not assume a disappeared target will circulate back.
