---
name: scene-memory
description: Preserve identities, reference images and event order across occlusion, demonstrations and changing arrangements. Use when a past observation, demonstration handoff or external event determines the next action.
---

# Keep references, events and physical progress separate

Use `general/gripper/state-verification/SKILL.md` for relationship evidence and
completion. This reference preserves identities, initial references and events
needed to choose the next operation.

## Retain identity through changes

Assign distinct identities to similar instances before moving or covering them.
Record any initial positions, orientations or order that the goal refers to;
do not overwrite those references with the objects' later locations. Update
identity-to-location links through observed transfers, not planned endpoints.

A picture or demonstration can establish identity or order without giving a
current executable world position. Match visible distinguishing features and
localize the actual object separately. Preserve identity through observed
transfers; an intended destination cannot identify an unseen face. Derive an
ordered or symbolic plan from the instruction and visible instances, keeping
that plan separate from the confirmed physical relationships.

## Observe a relevant event, then act

Keep a demonstration or another actor's progress separate from your own. Record
an event when a transition is observed, not once per frame showing the same
state. Preserve ambiguous or missed transitions as uncertain.

Give waiting a specific observable purpose and leave the waiting phase when
that event is established. If a later action confirms an earlier operation,
verify that operation before submitting the confirmation; blind replay can
advance the sequence twice.

## Separate sequence recognition from the execution handoff

The hold/step examples below assume a step-driven environment. In a continuously
running environment, use fresh time-associated observations and account for sensor
latency; waiting can change the scene even without a submitted robot action.

When the instruction requires observing a demonstration before acting, track
"sequence identified" separately from "demonstration completed; my phase started."
Inferring the last item or seeing its release does not establish the handoff.
Confirm the final demonstrated action and an explicit start signal or completed
withdrawal to a clear resting posture. "Withdrawing" is not "withdrawn"; a hand
still over the work area or a temporary pause is not sufficient evidence.

Without an explicit start signal, keep your arms in their waiting posture and
use short hold intervals to advance the demonstration. Inspect fresh observations
from successive physical steps: the final placement should remain stable and
the demonstrator should have completed withdrawal and remained at rest across
those observations. Judge the actor and relevant objects, not pixel equality of
the whole image. Static frames alone do not establish completion.

Check observation step identities. Re-reading the same state or sleeping after
queued motion ends advances no physics; apparent stillness then is not evidence
that the demonstration has finished. Wait for hold completion before comparing
its final observation with the previous one. If handoff evidence is ambiguous,
continue bounded waiting and observation rather than starting an exploratory move.

Reorientation and reachability tests are physical actions too, even without
object contact. Keep the waiting posture until the handoff is established.
This applies to observe-then-execute tasks, not explicitly concurrent interaction.

Keep observation checkpoints around placements and the expected phase transition;
do not batch long waits across unresolved events. Use no fixed waiting duration
or step count as a substitute for completion evidence. If an event was missed,
retain uncertainty and inspect recorded observations before relying on its order.

## Keep historical references separate from live state

Associate relevant observations with their capture step or time. Keep the initial
goal reference, latest observed geometry and last confirmed relation separate.
A historical view requested for recognition does not reset the scene or establish
the current object position. After compaction, retain pending job IDs and resolve
them before planning from a fresh completed observation; a queued endpoint is
not an observed state.

A segmentation match on a reference picture can identify the depicted object
without locating its physical instance. Verify scene membership and visible
material before using that geometry for contact. If the object moves, preserve
its identity but refresh contact coordinates using
`general/gripper/moving-target-control/SKILL.md`.

Preserve the original goal reference through compaction as well as movement.
A convenient canonical orientation must not replace a requested historical pose.
For asymmetric objects, retain directed distinguishing features, not only an
undirected axis or bounding box. If pose or order references need a compact
record, read `general/gripper/scene-memory/references/pose-and-order-memory.md`.
