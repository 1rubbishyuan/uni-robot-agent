# Navigation with a learned actor

Read the task's complete route and inspect the front camera. Preserve route
landmarks and turn order when instructing actor_act. Do not invent metric robot
positions or claim hidden goal distances from the simulator.

Use the base control group and empty constraints. A single actor action is a
bounded segment, with observation available between predictions. Smaller chunk
budgets return control to the planner sooner; they do not alter the locomotion
controller's 50 Hz native loop.

Compare successive images to determine whether a turn changed the view or a
forward segment produced visible progress. A commanded distance is not measured
travel, and a stopped actor does not certify arrival. If the route appears
complete, inspect again and explicitly finish. If visibility is ambiguous, stop
and reason from new observations before choosing another actor call.

The model's input consists of eight samples from episode history, including
marked black padding at the beginning. This is not eight different cameras and
not necessarily the last eight frames. Preserve actual source frames when
reviewing a trajectory. Native inference errors produce no fallback movement.
