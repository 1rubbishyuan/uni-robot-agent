---
name: handover
description: Transfer a held object between two available robot hands while preserving support and verifying independent receiver retention. Use for hand-to-hand transfer, not ordinary single-arm placement.
---

# Transfer support between hands

Use after the common attachment checks in `general/SKILL.md`.
Name the donor, receiver and intended object. Choose the receiving grasp for
the next operation, leaving an insertion tip or required placement face free.
Use current geometry; do not replay coordinates from another episode.

## Present an accessible grasp

- Confirm the donor actually holds the intended object. A wrist image centered
  on an object still at its source is not attachment evidence.
- Select a presentation pose both arms can reach with clearance for the whole
  object, fingers and forearms. Check that the receiver can approach the
  exposed material without occupying the donor's grasp region.
- Keep the donor closed while moving to that pose. Separate large changes in
  orientation from the receiver's final approach and inspect for slip after each.
- Hold the donor approximately stationary during receiver alignment. Approach
  with the receiver open; use view_env_state at alignment and contact checkpoints
  to inspect useful wrist and head views. If the donor obstructs the grasp, change the presentation
  while preserving its support rather than driving the receiver through it.

## Close, transfer support, then verify

1. Align receiver fingers around exposed object material, not around the donor
   fingers. Close the receiver while the donor continues supporting the object.
2. After receiver closure completes, inspect its attached wrist image (or VLA's
   final images); use view_env_state if another view is needed. Require visible
   enclosure of the intended object by the
   receiving fingers; closure or a valid tool return alone is insufficient.
   Two hands touching the object do not yet prove independent receiver retention.
3. Keep the receiver closed and approximately stationary while releasing the
   donor. Separate the open donor a short distance along a clear route, then
   call view_env_state to inspect whether the receiver alone retains the object.
   In parallel, do not queue donor release before the enclosure check or a long
   receiver carry before this independent-retention check.
4. Make a short receiver carry and verify the object follows without visible
   slip. Only then mark handover complete and continue toward the destination.

For Direct calls, preserve the holding hand's closed command during each
phase. For VLA, describe one phase at a time near closure and release; inspect
the result after each whole actor_act call. Use separate calls when an enclosure
or retention check must precede the next phase; the main agent does not inspect
between internal chunks. `constraints.gripper_openings_m` (or normalized `constraints.gripper_targets`,
when declared by the schema) fixes a hand's opening throughout the
whole call, so do not fix a hand that must close or release during that call.

## Recover without losing the remaining support

- If the receiver closes beside the object or the intended material remains
  outside its fingers, keep the donor closed and stationary. Reopen the receiver,
  correct its position relative to the visible material, and close again. Do not
  translate closed receiver fingers through the donor's grip to fix a miss.
- If a top-down receiver path is blocked by the donor or its forearm, retract
  the open receiver and inspect a side-entry route to exposed material. Change
  the donor's presentation only while its grip remains secure; inspect retention
  and the new corridor before another receiver approach. Do not rotate both
  hands around an unverified shared grip.
- For thin coins, slices or a broom handle, distinguish graspable object material
  from the donor fingers. If a mask selects the robot, clear the occlusion and
  remeasure. Repeated receiver misses call for a different exposed region or
  presentation, not another closure at the same relationship.
- If the object slips during support transfer, inspect its actual location
  before another move. When it still has donor support, recover receiver alignment;
  when it has fallen, reopen the acquisition subgoal at its new location and
  invalidate the old transfer poses. Do not move both closed hands apart and pull
  against the object.
- When intermediate placement is allowed, a stable support reachable by both
  hands can divide an otherwise unreachable route. Check the resting orientation
  and regrasp region before release; laying a thin object flat can remove its
  useful edge. This is a new placement and acquisition, not a substitute for an
  explicitly required hand-to-hand transfer.
