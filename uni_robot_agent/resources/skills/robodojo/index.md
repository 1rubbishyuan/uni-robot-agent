---
name: robodojo-index
description: Entry point for shared manipulation skills and RoboDojo-specific robot, observation, motion and return contracts.
---

# RoboDojo skill index

Read `general/index.md` and the common `general/SKILL.md` first. The general
index routes operations, geometry, evidence checks and recovery; reuse that
single library rather than maintaining a RoboDojo copy. Select references by
the current instruction and observations, not a task identifier or object recipe.

Read the following supplements when their condition applies. Reuse guidance
already in context; do not load every file or reread it per action.

| Path | Read when |
|---|---|
| `robodojo/robot-reference.md` | X5 frames, grasp site, hand dimensions, calibrated opening commands or measured jaw feedback |
| `robodojo/perception-reference.md` | Slot-local coordinates, camera calibration, pixel conventions or RGB/depth capabilities |
| `robodojo/gripper/motion-control/SKILL.md` | Native IK, temporary execution goals, planning rejection, recovery diagnostics or motion limits |
| `robodojo/gripper/return-to-start/SKILL.md` | Applying finishing-posture requirements and native link6 return semantics |

Use list_skills without arguments to discover all Markdown paths in general/
and this session's environment namespace. Pass a returned path to read_skill.
Guidance does not establish physical state or override the current instruction,
robot capabilities, tool contracts or stopping conditions.
