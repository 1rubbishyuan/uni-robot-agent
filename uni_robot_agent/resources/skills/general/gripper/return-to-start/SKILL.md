---
name: return-to-start
description: Return empty arms to their measured initial poses and verify the finishing posture. Use before returning an arm or checking whether it has returned.
---

# Return to the measured initial pose

The integration and task instructions determine when return is required and its
acceptance tolerances. Restore both position and orientation within those
tolerances. Moving clear of the objects, reaching a familiar XYZ, or receiving
pose_reached does not establish that the arm has returned.

## Recover the correct target

When recorded initial observations are exposed, use view_env_state with step=0
to recover each arm's initial measured pose; otherwise retain the initial measured
state supplied by the environment.
Keep each arm's own position and quaternion together.

Use move with that arm's initial position and its quaternion converted to
fixed-axis XYZ Euler radians in pose=[x,y,z,roll,pitch,yaw], both
measured at its registered grasp site. Choose the motion profile and tolerances
from the tool contract. Do not retain a top-down grasp orientation when the
initial orientation differs. Use the environment's declared return reference and acceptance criteria.
If its scoring reference differs from the control point, use the published
relationship rather than transferring a position tolerance between frames. Use
`general/gripper/pose-targeting/SKILL.md` when the grasp site or rotation
semantics are unclear.

Returning is physical motion within the current episode. Do not reset the
environment, recalibrate the robot, or substitute assumed zero joint angles.

## Plan a clear return

First establish stable support for any held object and verify release.
Preserve contact when the task still requires the arm to support or hold
something; do not return that arm prematurely.

Withdraw clear of the object before a large translation or reorientation.
Check the swept path of the fingers and both arms, not just the endpoints.
Use intermediate poses where needed. Coordinate both arms only when their
routes do not interfere; otherwise return them in a suitable order.

## Verify the complete pose

Resolve submitted motion before inspecting its final state. Compare the
measured grasp-site position AND orientation with the initial control_pose, using
the same reference frame. Check all available arms, including any that may have moved
unintentionally.

pose_reached refers to the target supplied to that call. Verify that the
supplied target was the intended initial pose before relying on that flag.
If either position or orientation is outside the required tolerance, correct
the observed mismatch along a clear path and inspect the result.

Check that withdrawal left the objects supported and undisturbed.
A successful return establishes the finishing posture, not task completion.
