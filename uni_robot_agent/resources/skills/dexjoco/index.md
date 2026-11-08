# DexJoCo actor control

Use actor_act for a bounded manipulation phase with the active groups reported
by its schema. The initial supported task is pick_bucket, using the right Panda
arm with its Allegro hand. Review modes are student and stop; constraints are
empty. No Direct or individual finger-control tools are provided.

Inspect all available RGB views before choosing a phase. Describe the local
physical effect needed by the task without inventing coordinates or finger
commands. A shorter actions_per_chunk gives more opportunities to observe and
review; max_chunks bounds repeated inference under one instruction.

TCP targets and sixteen finger position targets are applied together. The hand
has no single open/closed state. Contact, finger closure and target tracking alone
do not establish stable attachment. Check object motion and the required final
relation in subsequent images.

If motion fails or the scene becomes ambiguous, observe again and revise the
local instruction. Do not repeatedly issue an unchanged action after an unknown
execution acknowledgement; the harness must reconcile it first.

Call finish after the task's visible requirements are satisfied or after an
unrecoverable failure. The native evaluator records the official result; a
finish claim or reviewer phase_complete does not set that result.
