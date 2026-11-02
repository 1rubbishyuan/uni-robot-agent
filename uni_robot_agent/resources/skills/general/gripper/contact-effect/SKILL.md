---
name: contact-effect
description: Verify and adjust contact from observable effects on the target. Use for a new or uncertain contact, or after grip, working geometry or support changes during pushing, pressing, insertion or tool use.
---

# Test the effect at the target

## Predict, probe and compare

Before an unverified contact, identify the working surface and one observable
change expected on the target. Choose a short, controlled probe large enough to
distinguish outcomes while limiting disturbance. Set its extent from the current
geometry, clearance and tool limits; there is no universal distance or duration.
Use an existing pre-action view as the reference when it is still valid.

Inspect the target after execution. Distinguish movement in the intended direction
from rotation, tipping, slip or motion of the support. Evidence sufficient for
one layer below does not automatically establish the next.

| Evidence | Establishes | Still needs evidence |
|---|---|---|
| Hand or wrist moved | The command produced robot motion. | The intended object or tool followed. |
| Held object or tool followed | Control over that object during the observed motion. | Its working surface acted effectively on the target. |
| Target changed | A physical effect occurred. | The effect matches the required direction, state or event. |
| Required effect observed | The local requirement has evidence. | Other requirements and final relationships are satisfied. |

## Inspect the relevant response

| Contact | Compare before and after | When the response is wrong or absent |
|---|---|---|
| Push or slide | Target translation, rotation, tipping and maintained support. | Change the contact point, height or direction rather than extending the same ineffective stroke. |
| Held-tool contact | Working end relative to the grip, its target contact and the target response. | Separate grip slip from ineffective contact; re-localize the changed working geometry. |
| Press or trigger | Local displacement, visible state change or an available activation signal; release when another cycle requires it. | Keep contact, mechanical movement and functional activation distinct. |
| Insert or engage | Entry at the opening, exposed length, relative axes and edge deflection. | If wedged or tipped, withdraw safely before correcting alignment. |
| Constrained motion | Moving part relative to its base, and any unintended base movement. | Restore support or revise the contact if the whole assembly moves instead. |

A clear visual state change can confirm an effect without a separate event
signal. Conversely, apparent touching or pose_reached alone cannot establish
activation. Use only observations and feedback exposed by the tools; do not
assume access to hidden success flags, counters or simulator state.

## Revise the failed hypothesis

If the expected change is absent, distinguish missed contact, slipping grip,
blocked target or an unsuitable force direction using available evidence.
Keep an unobserved cause as a hypothesis. Change the relevant contact factor and
test again; a longer stroke or unchanged repetition may preserve the failure.
With a held tool, locate the working end again after slip or reorientation.

## Establish an effective press before repeating it

Identify the movable button cap, its pressing direction, and an observable
response that distinguishes activation from mere contact. Record a useful
unloaded baseline while the cap is visible.

Align the actual contacting part of the finger or held tool with the cap.
The finger-center target is not automatically the physical contact point.
Approach with clearance, then use bounded contact motion along the intended
pressing direction.

Inspect the cap's travel or a visible functional response. Hand descent,
pose_reached, a tracking stall, or firm-looking contact alone does not prove
activation. A spring-return button may already be raised in a post-release
image; inspect evidence from the pressed state as well as the released state.

If the response is absent or ambiguous, keep the press unconfirmed:

- Check whether contact is on the movable cap or the fixed housing.
- Check alignment, pressing direction, obstruction and unintended base motion.
- Compare actual motion and target error with the required contact geometry.
- Check whether arrival tolerance allowed stopping before an effective press;
  tighten it only when the target and clearance justify it.
- Then choose a small additional stroke, a corrected approach, or another
  contact geometry based on the observed cause.

Do not conclude that a button is rigid or touch-activated merely because a
press failed to move it. Do not keep lowering an unchanged target when the
hand is stalled or the assembly is moving instead.

## Reuse verified effects and count events

Establish one effective press-and-release cycle before repeating its motion.
Count a press only when the required effect has supporting evidence, and
establish release or rearming before the next cycle.

Reuse a verified stroke while grip, working geometry, target and support remain
compatible. Recheck after slip, collision, changed alignment or an unexpected
response, not every known segment. Keep an observation boundary before motion
that depends on an unresolved effect; known approaches can still be batched.
Do not batch further unverified cycles to satisfy a numerical target.
For tool strokes, disengage the working end before lateral travel.

Count confirmed effects or distinct observed transitions, not calls, hand
travel or frames showing the same state. For exact-count tasks, keep confirmed
and uncertain events separate. Inspect an ambiguous attempt before repeating
it: an unseen activation could otherwise be counted twice. Tool-call history
proves attempted cycles, not the number of successful presses.
Use `general/gripper/state-verification/SKILL.md` when the remaining question
concerns the object's resulting state or completion evidence across operations.

A calibration probe is a real interaction and can already contribute to the
required event count. Do not reset the physical count when switching from
calibration to execution. If exact count or ambiguous rearming matters, read
`general/gripper/contact-effect/references/exact-count-cycles.md`.
