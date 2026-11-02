For RoboDojo actor_act, pass evidence-backed task_progress:
verified_completed, currently_attempting and remaining subgoals. This is
model-reported progress, not an environment verification flag. Correct it when
a previously achieved relation is disturbed.

When enabled, the RoboDojo reviewer assesses each fresh Pi proposal before its
prefix executes. It receives current images and measured state, recent
feedback, previous actor context and robot-only FK, not the agent's full
conversation. Previous actor images may predate intervening Direct actions;
they are not guaranteed before-images of the latest action. FK describes robot
kinematics, not future object motion, attachment or collision-free passage.

An assessment describes evidence available before that chunk executes. It may
precede actor_act's final returned state, so verify later effects from the
returned images and state. A reviewer stop or phase_complete report is evidence
to inspect, not proof of global completion. Inspect whole-call execution totals
when an early stop, partial execution or error follows earlier chunks.
