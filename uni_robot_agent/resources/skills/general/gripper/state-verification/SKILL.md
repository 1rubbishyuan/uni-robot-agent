---
name: state-verification
description: Verify object states and relationships from distinguishing evidence. Use when the next action depends on an unresolved state or when assessing completion across occlusion and multiple objects.
---

# Verify the relation that changes the next decision

## Select one unresolved question

Identify the unresolved relation that matters for the next dependent action:
attachment, support, containment, orientation, identity or a required effect.
Derive requirements from the instruction; add no preferred pose, count or order.
Choose an observation whose possible outcomes would change the next decision.
Use existing images and feedback first. Select a new view only when it can
distinguish the remaining outcomes; no written proof is required per call.

Keep three outcomes: confirmed, contradicted and unresolved. A tool return or
absence of visible failure does not by itself confirm an object relationship.

| Question | Distinguishing evidence | Insufficient alone |
|---|---|---|
| Is the intended object controlled? | A safe test motion preserves its relation to the hand, with consistent identity and source-view change. | Closed fingers, opening residual, or a centered wrist image. |
| Is it supported after placement? | After release and clear withdrawal, it remains on the intended support. | The hand reached the placement pose. |
| Is it contained or engaged? | Visible opening, edge and depth relationships support entry rather than bridging or leaning. | Overlapping silhouettes. |
| Is its required orientation correct? | Identifiable ends, faces or axes distinguish the requested orientation. | Correct center position or an ambiguous long axis. |
| Are all required objects accounted for? | Each identity has an observed destination or a still-valid chain of confirmed transfers and placements. | Attempt counts or one final merged silhouette. |

A verification lift is appropriate when lifting is intended and observed
clearance permits it. For constrained or fragile objects, choose a compatible
test; do not impose a lift on every operation. Inspect the resulting object
motion rather than treating the test command as evidence. For an uncertain
contact effect, use `general/gripper/contact-effect/SKILL.md`.

## Compare the right records and views

Choose the camera that exposes the deciding feature rather than requesting all
views by default. For attachment, compare the original support and the hand;
for engagement, expose the remaining gap or entering feature. A second view
is useful when it resolves identity, occlusion or competing explanations.

view_env_state(step=...) selects a recorded observation index, not a native
physics step. Use returned record_index and step to identify the before/after
states. Re-reading the latest record advances no physics and does not wait for
an in-flight action. Resolve that action before checking its final effect.
When a physical process needs time, use a short hold for a specific observable
purpose, obtain its completed result, and inspect the attached image.

Track the same object across records and account for camera movement. An object
centered in a moving wrist image may still rest on its original support; image
displacement alone cannot separate object motion from camera motion.

After a verification lift, compare the same object against its original support
and the hand. If the hand rises but the object stays at its source and becomes
smaller in the wrist view, the grasp did not retain it. A centered image is not
counter-evidence: the camera moved with the hand. When the views cannot resolve
height or attachment, use a distinguishing view or a calibrated measurement of
the same visible feature before dependent transport. Do not measure again when
existing observations already establish the relation. If lifting is inappropriate,
choose a compatible probe. For a press, inspect target displacement and release
rather than counting reached hand poses; use the contact-effect skill for event counts.

## Preserve a small evidence chain

Retain only the identity, required relation, its status and the observation or
transition supporting it. Update affected entries; do not narrate a full ledger
after every call. A brief record such as "A released onto B; support observed"
can preserve evidence that will later be occluded. Include the observation's
step or time when needed to distinguish it from an older view. For nested or
stacked objects, retain each established support relation; a final merged outline
cannot retroactively confirm an unverified entry or repair a disturbed base.

A confirmed relation can remain valid after occlusion. Later placements need
not expose every earlier object again if identities and transfers were tracked
and subsequent actions give no reason to suspect disturbance. An unverified
relation does not become confirmed merely because the object disappeared.

A collision, slip, changed support or ambiguous identity can invalidate affected
relations and those depending on them. A relation depending on a displaced base
needs review even if its own object was not directly touched. Recheck those
relations rather than restarting the whole arrangement. During occluded actions
that could change a relation, preserve uncertainty until useful evidence returns.

## End the check with a decision

- Confirmed: continue the dependent action without duplicate views.
- Contradicted: correct the observed mismatch and update the affected relation.
- Unresolved: choose one useful view or safe probe that separates the alternatives
  before committing to a dependent action. Prefer the least disruptive check.

If the same view adds no information, change the view, probe or hypothesis.
If available tools cannot distinguish the alternatives, keep the relation
unresolved and pursue a different feasible observation, approach or independent
subgoal when order permits. Longer explanations do not strengthen the evidence.

For completion, combine current observations with earlier evidence that remains
valid. Check required relationships and their dependencies, not whether all
planned actions were attempted. Report remaining uncertainty faithfully; follow
the system prompt's stopping conditions. Uncertainty does not authorize success
or an early finish, and does not justify repeating an uninformative check.

When exposed, read `state.arms[arm].metadata.gripper_opening_m` for actual
signed pad gap and `gripper_target_opening_m` for the effective drive target.
Unavailable measurements remain unknown; a residual gap alone does not establish
attachment.
Read the current environment's robot description and published manual for
units, frames and capability limits.
