---
name: robodojo-motion-control
description: Interpret RoboDojo native IK, temporary recovery goals, motion guards and diagnostic counters.
---

# Interpret RoboDojo motion and recovery

Use `general/gripper/pose-targeting/SKILL.md` to construct pose targets and
`general/gripper/motion-recovery/SKILL.md` to choose a changed recovery from
observed evidence. The details below describe this backend, not other robots.

## Waypoint planning and temporary goals

Profiles set waypoint limits and final arrival tolerances; they add no approach
height. RoboDojo keeps strict native waypoint IK (1 mm / 0.02 rad). After a
planning rejection, one recovery search per move may choose a temporary execution
goal within 80% of the original final position and orientation tolerances.
The requested target remains unchanged for measured arrival checks. Read
`goal_recovery` and per-arm `execution_target_position`/`execution_target_quaternion`
to distinguish the route's temporary endpoint from the original `target_pose`.
There is no automatic return to the exact goal center or coarse-to-fine phase.
Choose tolerances that the intended operation permits before submitting move.

## Recovery diagnostics

Per-arm `recovery.rejected` records stages for finally rejected candidates.
`native_not_converged` and `native_constraint_rejected` are distinct; a candidate
can have both. `collision_or_native_constraint` means a sampled joint connection
failed a native constraint check, not necessarily that endpoint IK failed.

`goal_recovery` records the optional once-per-move final-region search. A found
candidate is only a temporary execution goal: subsequent waypoints still need
IK and measured arrival must satisfy the original goal's tolerances. There is
no automatic tightening phase. Inspect actual feedback before dependent actions.

## Execution feedback and limits

A successful tool call does not prove physical arrival or task success. Inspect
`feedback.pose_reached`, actual position/orientation, executed steps and stop
reason. `grasp` or `release` does not prove object attachment or detachment.
Use measured state and images to verify the intended effect.

The current native IK includes modelled self/table constraints. Other action
routes do not necessarily have identical checks. Endpoint checks and joint
jump guards do not establish collision-free swept motion; task objects, the
other arm and carried objects are not automatically represented by the public
robot geometry. Plan short observable approaches and check clearances.

This interface does not add a timed-trajectory guarantee. Control steps consume
native episode budget; model thinking and network delay are separate wall time.
On a partial or uncertain action, inspect returned state/status instead of
replaying the same physical request.

## IK recovery goals and counts

RoboDojo keeps each native waypoint solve at 1 mm / 0.02 rad. After a rejected
waypoint, move may search once for a temporary endpoint inside 80% of the original
final position/orientation tolerances. `target_pose` remains the requested goal;
`execution_target_position` and `execution_target_quaternion` identify the
possibly adjusted endpoint. `goal_recovery` reports that search, not physical
arrival. Actual pose must still satisfy the original tolerances; there is no
automatic return to the center or coarse-to-fine phase.

Primitive per-arm `recovery.stats` and move's aggregate `ik_statistics` count:

- `ik_requests`: one arm's strict waypoint planning request, including held arms
  when actually solved; arms skipped after an earlier rejection are not counted.
- `ik_request_failures`: requests still rejected after candidate recovery.
- `solver_calls` / `seeds_attempted`: 32 then 128 means two calls and 160 seeds.
- `candidates_returned`: distinct seed results, with the true top1/topN alias removed.
- `candidates_checked` / `candidates_rejected`: candidates examined / finally rejected.
  Raw and legal whole-turn representations are branches of one candidate.

`seed_budget` retains its existing meaning: the largest single solve's seed
budget, not the sum. Different seeds with equal joint vectors remain distinct.
`rejected` counts each finally rejected candidate at most once per stage; stages
can overlap, so their sum is not a count of failures. Raw top1 convergence, native
feasibility and pose errors are reported separately from its combined success.
`ik_candidate_rejected` covers validation/connection failures and mixed stages;
none of these finite searches proves the requested pose mathematically impossible.

The waypoint IK rejection rate is `ik_request_failures / ik_requests` when the
denominator is nonzero. Region-search calls are separately recorded under
`goal_recovery.diagnostics` and are excluded from that fraction. Do not sum move
aggregates and their primitive receipts together. Across logs, identify each
primitive request and arm once. `ik_statistics=null` means this backend provided
no such counters, not that zero IK requests failed.

## Recovery and episode stopping

IK failure and joint-jump rejection are recoverable planning feedback, not task
failure or an environment failure preventing interaction. While the task remains
incomplete, interaction is available and budget remains, continue recovery.
Neither one rejection nor repeated rejections justify finish, ending the turn,
or declaring the task impossible. Abandon an ineffective route, not the goal.
