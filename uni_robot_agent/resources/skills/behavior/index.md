# R1Pro mobile manipulation

Use whole-body actor control for local manipulation phases. Give concrete language
instructions grounded in the current head and wrist images, then inspect the
result before selecting another phase. Avoid repeating a command after an uncertain
receipt; the harness reconciles execution identity.

Base motion changes the geometry seen by both arms. Do not assume a previously
visible object remains within reach. If a bounded phase shows no useful progress,
stop, inspect fresh images and revise the instruction.

The binding can serve tasks sharing this R1Pro controller profile. The publicly
provided example checkpoint is trained for turning on a radio; changing a task
name or instruction does not establish broader learned competence.
