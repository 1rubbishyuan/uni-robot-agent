---
name: exact-count-cycles
description: Preserve confirmed and uncertain activations through calibration and repeated contact when exact event counts matter.
---

# Count effects across the whole interaction

A probe can cause the same physical event as an intended execution stroke.
There is no implied counter reset between calibration and execution.

Keep a small event record only when the instruction needs an exact count:
required count or order; confirmed activations; uncertain attempts; last
confirmed release/rearm; and the observation supporting the latest transition.
Include calibration attempts. Count one observed transition once, not once per
frame, command or continued downward motion.

Before another cycle, establish that the target released or rearmed as required
by its observed mechanism. Several downward commands during one sustained
contact do not establish several activations. A raised cap after release does
not by itself disprove a preceding activation; retain the pressed-state evidence.

Resolve an ambiguous attempt using available observations before repeating it.
If the event remains unobservable, retain uncertainty instead of declaring zero
and adding another stroke. Do not claim an exact count from tool-call history or
invent a hidden counter. Reuse a verified stroke only while its geometry and
support remain valid; do not batch across an unresolved effect or rearm boundary.
