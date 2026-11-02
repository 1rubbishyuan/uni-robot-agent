---
name: evidence-driven-gripper
description: Choose and verify contact operations, placements and recoveries from current observations and the available gripper capabilities across tasks and environments.
---

# Evidence-driven gripper

Choose the operation from the current instruction and observations. Push or slide
an object on its support when requested; a grasp-and-lift is not a substitute.
Pressing needs visible contact and effect, not attachment. The acquisition and
transport checks below apply when lifting is appropriate.

## Apply the current robot and runtime contract

Use only the arms, tools and feedback capabilities exposed in this session.
Two-hand guidance applies when two arms are available; single-arm tasks can use
existing support or a permitted supported relay. Tool examples assume the stated
uni-robot-agent interface, not universal robot geometry. Read the environment's robot
manual and declared calibration when needed. Follow the current scheduling and
physics-advancement contract before applying batching or waiting examples.

## Plan the available hands' roles before committing

Before acquisition or a new contact operation, assess the available hands against
the complete route. Assign the needed roles: carry, restrain, reposition the receiver,
receive a transfer, or remain clear. Do not default to the first-used arm for
every phase. Preserve any instructed hand assignment and existing support.
When one hand holds the load and the receiver needs adjustment, assess whether
the free hand can establish a usable destination without losing that hold.
Read `general/gripper/supported-contact/SKILL.md` when the operation needs control
of both objects or an assisting hand must adjust or restrain the receiver.

Repeated single-arm route failures require reassessing hand assignments before
another attempt; use `general/gripper/reachability-and-recovery/SKILL.md`.
Coordination can be sequential. Keep a reliable single-arm route when existing
support suffices; moving both arms adds no value without a useful second role.

Coordinated Direct calls can move both hands together, but do not guarantee
collision-free paths, synchronized arrival or a fixed relative pose. Read each
arm's feedback and keep checkpoints where the next motion depends on new evidence.
Use `general/gripper/pose-targeting/SKILL.md` for constructing pose targets.

## Keep a small evidence ledger

For each relevant object, retain its identity, required relation, confirmation
status and supporting observation. Track the operation actually needed; push,
press and insert operations need not follow a pickup-to-placement sequence.
Use `general/gripper/state-verification/SKILL.md` for unresolved states, evidence
chains and completion checks. Use `general/gripper/contact-effect/SKILL.md` when
contact effects need testing or correction. Read these when needed, not per call.
Derive quantities, order and final relationships from the current instruction.

## Reuse coordinates only while their evidence remains valid

Use existing evidence to choose the next action. Use view_env_state for missing
visual evidence or hand state, and capture_scene when new coordinates are needed.
Snapshots remain readable historical measurements after physical actions.
Their coordinates remain useful while the objects, supports and relevant
geometry remain unchanged; a readable snapshot need not describe the current scene.
Re-localize affected features after slip, displacement or uncertain release;
known lifts and clear waypoints can use measured hand state. A mask median is
not automatically a grasp pose. Use the tool-selection skill when repeated
queries fail to resolve the next action, rather than restarting scene mapping.

## Separate alignment, contact and attachment

- Alignment means the hand reached the requested pose within its tolerance.
  Before closure, compare the actual contact-region pose with the grasp margin;
  pose_reached can still leave a thin object below the fingers. Use the arrival
  and slip cases in `general/gripper/grasp-geometry/SKILL.md` when evidence conflicts.
- `call_ok` reports a valid call, not motion or object success. Read the completed
  action's stop reason, executed steps and measured state. Hand geometry and
  opening do not establish attachment; compare object motion and its support.

## Verify before committing to transport

1. Retain a pre-lift observation and the target's source location. Check visible
   material and clearance for both fingers before closing. A mask center may lie
   in a hole or between objects; it is not automatically a graspable point.
2. After closing, inspect execution feedback and the attached operating-side
   wrist image; use view_env_state if another view is needed. If visibly empty,
   reopen and realign. When lifting is intended and clearance permits, use a
   small verification lift. Compare source and hand views: did the intended
   object leave its support and follow the hand? A centered wrist image alone
   is insufficient. For constrained or fragile objects, choose a compatible
   probe. Dependent transport or insertion waits for confirmed control.
3. If attachment is confirmed, continue without extra numeric proof. If evidence
   conflicts, use a distinguishing view or compatible probe; missing segmentation
   alone does not prove loss. Use the state-verification skill for this decision.
4. Begin loaded carry with a short segment at the same orientation and confirm
   retention. Then follow the known clear route to the next meaningful checkpoint.
   Recheck changed load/support after substantial reorientation, obstacle contact
   or transfer, and check retention before release. On loss, reopen acquisition.

For Direct verification and initial loaded transport, use short
move(profile="precision") segments with the same
orientation and a grip command compatible with the observed hold. A command does
not guarantee retention. If the object shifts, revise the contact geometry and
motion using `general/gripper/grasp-geometry/SKILL.md`. Do not enlarge motion
increments just because the load is above the table; larger steps can cause slip.
Use the exposed tool contract for limits and defaults.

When actor_act is exposed, request one observable local phase and use its declared
reviewer and chunk capabilities. Do not combine acquisition, transport and release
or cross an unverified release boundary. The main agent inspects the result after
the whole call; split phases into separate calls when a decision needs intervening
evidence. Do not require a Direct setup sequence before every VLA action.

## Batch the known route, stop at unresolved decisions

In parallel mode, submit already-determined independent queries or known route
segments in one response, ending at the next unresolved decision. In particular,
measure a manipuland and its receiver together when both queries are known.
During a known clear motion, query an existing snapshot of an unaffected target
before waiting. Read the batch examples in
`general/gripper/tool-selection/SKILL.md` when choosing these boundaries.
Preserve closure, attachment, support-transfer and release checks when later
motion depends on their effects. Once confirmed, continue without another check
for each small waypoint. Ordered execution does not certify route clearance.

When a new locate query is already required after a planned physical action,
submit that action followed by capture_scene in the same response. The shared
physical FIFO captures after the action finishes. Receive its snapshot_id, then
batch independent locate queries on that snapshot in the next response. If the
need for localization depends on the action outcome, inspect the result first;
for visual verification alone, reuse the action's returned images.

Before a route crosses the shared workspace, inspect both wrists, forearms and
held objects, even when only one arm will move. Identify the other arm's role:
an empty idle arm can withdraw along a clear route; a hand supporting a container
or holding an object cannot simply be parked away. Adjust the moving route or
arrange another stable support without dropping that role.

If motion stalls near the other arm, treat interference as a hypothesis, not a
proven cause. Check actual pose and visible clearance. After changing the route
or moving the obstructing arm, verify that change completed, then test the
previously blocked segment before batching the rest. Merely announcing that the
other arm is clear is insufficient. Shorter steps along the same blocked line
can still stall and consume physics.

Follow the runtime queue and barrier rules when resolving submitted work before
recovery. A tail result does not prove an intermediate grasp worked. Once
clearance and retention are established, resume the known route without making
every free-space waypoint a separate decision.

## Preserve support and plan the whole route

Use `general/gripper/reachability-and-recovery/SKILL.md` when arm choice or
destination access is uncertain, `general/gripper/held-object-geometry/SKILL.md`
when the load's actual extent determines clearance, and
`general/gripper/moving-target-control/SKILL.md` when contact geometry moves.

- Choose grasps compatible with the assigned roles and full route; plan any
  necessary relay before pickup. A failed pose does not prove all poses fail.
- Reuse an already stable, reachable base instead of moving it for a uniform
  setup routine. Move it when the layout blocks later placements or withdrawal.
  Preserve useful racks and containers; follow any instructed hand-support role
  rather than assuming every container should be left on the table.
- Plan clearance for the held object, both fingers and the forearms. A wrist or
  finger center above a rim does not prove the bottom of the load clears it.
- For a hand-to-hand transfer, read general/gripper/handover/SKILL.md. Keep donor
  support until receiver closure, then verify independent retention after donor
  release and separation. Leave the receiving grasp and insertion features free.
- Use a supported relay only when intermediate placement is allowed and direct
  transfer is infeasible. Check both arms' access before release; re-localize and
  verify the new grasp afterward. Laying a thin object flat can remove its useful
  grasp edge. A table relay does not fulfill an explicit hand-to-hand instruction.
- Before placing, plan the empty-hand AND forearm withdrawal corridor. Release
  into stable support, withdraw clear and inspect the actual object and neighbors.
  If withdrawal disturbs the arrangement, reopen affected ledger entries.

## Match recovery to the observed failure

| Feedback or observation | Next decision |
|---|---|
| max_steps with decreasing pose error | Continue toward the same absolute target if the route remains clear. |
| IK failure, joint_jump_rejected or unsafe_motion | Read executed steps and current state; use `general/gripper/motion-recovery/SKILL.md` to choose a changed, checkable recovery. |
| stalled or diverging | Check visible obstructions and actual tracking. Withdraw from contact before changing approach; do not merely raise the budget or push deeper. |
| Hand closes but object stays at source | Check actual arrival error first. If alignment was sufficient, inspect material and two-finger clearance, then change grasp region or closing direction. |
| Object lifts but slips in transport | Re-localize, then revise the grip/support relationship and route. Slower repetition of the same weak grasp is not necessarily an improvement. |
| Object tips or neighbors move after placement | Reopen affected items and restore stable support before continuing nearby work. |

Use `general/gripper/motion-recovery/SKILL.md` for partial execution, difficult
reorientation and changed recovery routes. A previously reached pose does not
certify the route back or clearance with a load. Separate substantial
reorientation from carrying a fragile load, checking clearance and attachment.
Tighter pose tracking does not prove object placement.

## Spend recovery effort on a changed hypothesis

After unsuccessful attempts, distinguish the observed effect from a hypothesized
cause. Change the relevant material region, closing axis, approach, support,
route or arm based on that evidence. Avoid
repeating an unchanged contact relationship without new evidence. If order is
unconstrained, keep a difficult object pending and work on another reachable one.
For a prescribed sequence, resolve the current item rather than skipping it.
Use remaining_steps and costs observed in this episode to retain room for the
remaining acquisition, transport, placement checks and safe withdrawal. Do not
spend the remaining budget refining an already verified pickup.

## Inspect the physical result

Check the instructed object relationships using current observations and
still-valid process evidence. Occlusion alone does not invalidate a confirmed
relation; an unverified relation remains unresolved. Recheck affected relations
and their dependencies after a disturbance, using the state-verification skill.
Use available action images or view_env_state for missing visual evidence, and
calibrated geometry only when a correction needs new coordinates. Withdraw empty
hands through clear space without disturbing the result. Complete the return
required by the integration or task, using
`general/gripper/return-to-start/SKILL.md`; do not add stricter pose requirements.
For an instructed lift distance, compare the same object's position before and
after pickup using visible geometry; hand displacement alone is insufficient,
especially after slip or reorientation. Track the object rather than a rising
corner caused only by rotation. Keep the measurement unresolved if occluded.
Report visible progress and uncertainty faithfully during recovery; follow the
system prompt's completion and stopping conditions.

For robot frames, hand dimensions, jaw commands and camera calibration, use the
current environment's published robot manual and capability description. Read
available calibration for asset-specific numbers rather than guessing from a
normalized drive value; metric openings require an exposed calibrated interface.
