---
name: robodojo-return-to-start
description: Apply RoboDojo finishing-posture and native link6 return semantics after the general return procedure.
---

# Apply the RoboDojo return reference

Use `general/gripper/return-to-start/SKILL.md` for release, clearance and pose
verification. Read each arm's initial grasp-site control_pose from step=0.
When no continued contact is required, return each used empty arm to that pose,
following the task and system prompt's finishing requirements. Verify position
and orientation, and check both arms are clear without disturbing the result.

Official native return-position thresholds are
measured at link6, not at the grasp site; orientation error can displace link6
even when the site position matches. Restore the full initial pose accurately.
Do not reinterpret a native link6 position threshold as a grasp-site position
tolerance or add stricter task requirements. Follow the current task's declared
thresholds; this skill does not supply a universal numeric return tolerance.
