The humanoid is one coordinated actor group. actor_act proposes upper-body joint
targets together with planar walking velocity and a pelvis-height reference.
Native whole-body control maintains the legs and waist. Neither direct gripper
tools nor independent leg/waist commands are available.

Use bounded actor calls and inspect fresh head-camera observations before deciding
the next phase. Keep instructions consistent with the selected task and checkpoint.
Language conditioning does not establish support for an unseen skill or task.

A reviewer may allow a student prefix or stop. It cannot edit whole-body geometry,
joint targets or locomotion references. constraints must be empty.

The simulation advances only while executing acknowledged control samples.
Thinking, inference and review do not consume simulated motion time. Previews
show requested controller references, not predicted contact, balance or motion.
An acknowledged action is not evidence of grasp or task success. The native task
evaluator owns the official outcome independently of finish and review decisions.
