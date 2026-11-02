---
name: motion-recovery
description: Avoid unproductive Direct motion and recover from IK rejection, joint jumps, stalled or diverging tracking, and unsafe motion. Read before a difficult reorientation or when a motion fails to progress.
---

# Avoid motion failures and recover

Choose a reachable route and task-compatible orientation from current evidence.
A failed pose is not proof that the operation or episode is impossible. Use the
common skill for support, attachment and already-submitted action boundaries.

## Before a difficult move

- Retain full poses that this arm actually reached, including orientation and
  whether the hand was empty or supporting an object. A requested but unreached
  pose is not a verified waypoint. A previously reached pose does not certify
  the route back to it or its clearance in a changed scene.
- Prefer an orientation already useful for this arm and operation when its
  geometry remains valid. A coordinate that is reachable with one orientation
  can fail with another. Do not change an instructed orientation just to move.
- Match the target to the registered grasp-site control_pose. Do not substitute
  a native controller pose or an object feature's XYZ without alignment. Use
  `general/gripper/pose-targeting/SKILL.md` for pose and rotation semantics.
- For substantial reorientation, allow clearance for the fingers, wrist,
  forearm and any held object. Consider orienting at a verified clear waypoint
  before approach, but do not require this for every move. Combined translation
  and rotation can improve reachability, but do not establish loaded stability.
  Preserve support and verify retention when changing a held object's orientation;
  an empty-hand recovery is not a validated carrying route. Empty space alone
  does not ensure IK.

## Diagnose from the returned state

Read executed steps, stop reason, per-arm state and any stop evidence exposed by
the current tools before deciding. The table applies when these feedback labels
are available; do not infer an unavailable diagnostic. For planning_rejected,
feedback.stop_evidence.reason identifies the rejected planning condition.

| Feedback | Interpretation and next decision |
|---|---|
| max_steps with decreasing errors | The call budget ended while tracking progressed. Continue toward the same absolute pose if the route remains valid. |
| stop_reason=planning_rejected, reason=ik_failed | Native IK produced no usable candidate for the next waypoint; inspect native convergence and feasibility evidence, not just the combined success flag. Earlier steps in the call may have executed. Inspect actual state and change the relevant approach or orientation. |
| stop_reason=planning_rejected, reason=joint_jump_rejected | Candidate rejection evidence contains only the enabled joint-jump guard; the next transition was not executed. Earlier steps may have executed; recover from measured state without disabling protection. |
| stop_reason=planning_rejected, reason=ik_candidate_rejected | Candidates failed pose/limit/connection checks or a mixture of stages. Inspect per-arm recovery.rejected; do not infer a single cause or mathematical unreachability. |
| stalled or diverging | Tracking stopped progressing or worsened; the cause is not established. Inspect clearance and contact before another attempt. |
| unsafe_motion | Measured motion exceeded a guard threshold. Inspect the resulting scene and both arms before issuing dependent motion. |

Use the current backend's published diagnostic meanings when additional
recovery fields are present. Keep candidate planning, executed motion and
measured arrival separate. A recovery candidate does not establish arrival.

An IK Success does not certify a smooth transition or collision-free route.
Do not edit joint angles, bypass the tools or try to repair IK by quaternion sign:
q and -q specify the same orientation. Increasing max_steps cannot cure repeated
zero-step rejection; making the same blocked line finer may still be ineffective.

## Recover without abandoning the task

IK failure and joint-jump rejection are planning feedback, not proof of task
impossibility. While the task remains incomplete and interaction is available,
use the task and runtime stopping rules and pursue a meaningful changed recovery.
Abandon an ineffective route rather than inferring that the whole goal is impossible.

Resolve submitted actions, then read steps_executed, the rejected arm and actual
poses. Earlier steps may have executed; a rejected next step does not roll them
back. Inspect current grip and support before changing a loaded hand's route.

Change one meaningful constraint supported by the evidence: an intermediate
position, approach direction, task-compatible orientation or suitable arm. For a
held object, preserve support and use bounded contact motion; if necessary,
restore stable support before regrasping or handing over. Do not automatically
open the gripper or apply an empty-hand reorientation to a fragile load.

Submit one recovery segment and verify motion and retention before dependent
work. If the state remains unchanged and the same rejection recurs, switch the
route or gripper strategy rather than generate tiny variants of that pose.
If order permits, make progress on another unfinished part and revisit this one.

## Case: a loaded turn finds IK but rejects the joint transition

A large turn stops with orientation error remaining. The next call reports
joint_jump_rejected; the recovery diagnostics show native candidate success and
joint-jump rejection. The overall planning status is Fail because no candidate
passed all checks. This establishes a rejected transition, not a proven hard joint limit or
an unreachable destination. Inspect actual pose and retention after any executed
steps. Do not continue the carry on the premise that the turn completed.
If observed clearance and the load permit, test a short route to a previously
reached compatible pose before reorienting. A direct return is not guaranteed.
For a fragile hold, restoring support and choosing a different grasp or arm may
be preferable to combined translation and a large loaded turn. Keep support until
the replacement is established. Verify progress and retention on the changed
segment, then resume the known route. A q/-q sign flip, more budget or disabling
the guard does not supply the missing route.

## Case: a basket approach stalls, or rejects before moving

If a call executes steps but stops short near a rim, inspect actual pose and
visible clearance of the load, fingers and forearm. A visible obstruction calls
for a clear withdrawal and changed approach, not a deeper target on the same line.
If the call rejects with ik_failed and zero executed steps, that attempt did not
move: compare a meaningful change of orientation, waypoint or arm using known
reach evidence. Neither result proves a fixed workspace boundary.
If contact is occluded, keep the cause unresolved and obtain a distinguishing
view or compatible probe. Once the failed constraint changes, test that segment
before batching the rest. For max_steps with decreasing error on a clear route,
continuing the same absolute goal can be appropriate; do not force a replan.

## Example: a partial move followed by unsafe motion

A motion executes several steps, then reports unsafe_motion. The hand is no
longer at its original pose, and neither the goal nor the reverse route is proven.
Wait for submitted work, inspect both arms and the object, and identify a clear
recovery route. If appropriate for the current load/support, move toward a
previously reached full pose using its matching position frame and orientation.
Observe the result, then choose a revised task approach. Do not blindly resend the
failed command, open a supporting gripper, or assume a direct return is safe.

## Example: a rotation needs continuation, not a new target

The call stops at max_steps while orientation error decreases and the route is
still clear. Continue with the same returned absolute `target_pose` in `move`.
Do not add the relative angle again; that would request a new rotation. If errors instead stall or diverge, reassess the pose and route;
extra budget is not evidence that the same attempt will recover.
