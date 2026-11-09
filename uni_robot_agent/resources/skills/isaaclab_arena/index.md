# Humanoid actor collaboration

Observe the current head-camera view and measured robot state before issuing a
bounded actor_act phase. The robot group coordinates both arms and hands with
walking and pelvis-height control. Its waist and legs are controlled by the
native whole-body policy, not by individual planner tools.

Choose an instruction grounded in the current task and visible scene. Shorter
prefixes permit earlier observation and review; max_chunks bounds repeated model
inference under one instruction. Changing the text cannot create a skill absent
from the selected checkpoint.

Look for the intended object motion and final relation in subsequent images.
Target tracking, closed fingers, locomotion and a completed chunk do not establish
success. If progress is ambiguous, obtain a fresh view and reassess the next phase.

A partial or unknown acknowledgement must be reconciled; do not repeat the motion
because its response is missing. Call finish after the visible task requirements
are satisfied or an unrecoverable failure. Native grading determines the result.
