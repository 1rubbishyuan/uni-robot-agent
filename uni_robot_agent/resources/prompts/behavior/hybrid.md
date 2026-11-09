Use actor_act with active_groups=["whole_body"] for bounded local phases.
R1Pro couples its mobile base, torso, arms and both grippers. Partial body groups
and Direct gripper tools are unavailable in this profile.

The actor returns 32 candidate samples; the default execution prefix is 16 samples
at 30 Hz. Every continuation observes again. Simulation advances only while
executing acknowledged commands and pauses while the agent or actor reasons.

Base commands use the robot-local frame: forward/lateral metres per second and
yaw radians per second. Arm and torso commands are absolute joint targets in
radians; the two fingers of each gripper receive symmetric metre targets.
Raw actor gripper values are normalized commands, not measured opening widths.

Previews describe requested commands, not predicted collisions or object motion.
The optional reviewer may approve a shorter actor prefix or stop; it cannot edit
individual joints, move a gripper directly, or guarantee a stable grasp.
Inspect fresh RGB observations before declaring a local phase complete.

Official task predicates and success are unavailable as tool feedback. The native
evaluator records the outcome after finish. This profile retains native assisted
grasping; completion of an execution request is not evidence of task success.
