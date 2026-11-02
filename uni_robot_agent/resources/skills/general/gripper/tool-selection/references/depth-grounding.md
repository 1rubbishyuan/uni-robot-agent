---
name: depth-grounding
description: Interpret back-projected visible surfaces before constructing contact poses; use for mixed surfaces or uncertain feature identity.
---

# From visible feature to contact target

This reference applies only to range-backed localization exposed by the current
tool schema. For RGB-only localization use
`general/gripper/tool-selection/references/rgb-grounding.md` instead.

This is general geometry guidance. Use the calibrated capture_scene and locate
methods exposed in the session; do not invent a separate back_project API.
Raw depth is not visible to the agent. Back-projection returns the surface
seen at the selected pixel, which may differ from the intended object feature.

1. Name the missing feature and its purpose: contact material, support level,
   entry boundary, or held working end. Select visually identifiable material
   in a calibrated snapshot. Use a wrist view if it resolves the ambiguity.
2. Inspect the query overlay and each returned point with its validity feedback.
   Check whether it belongs to the intended surface, a different object, the
   support, a finger, or background. A hole may return perfectly valid support
   depth. Similar heights alone do not establish that points share a surface.
3. Use an aggregate only when the contributing samples represent the intended
   feature. Do not average an object top with its support or a foreground finger.
   If points disagree enough to affect the action, resolve surface identity or
   change view; do not choose whichever coordinate fits the preferred plan.
4. Construct the contact pose separately. A surface sample does not provide an
   unseen underside, thickness, center of mass or surface normal. Combine the
   observed contact region with calibrated hand geometry, closing direction,
   two-finger access and support clearance. Mark uncertain offsets as uncertain;
   do not recalibrate the robot from an ambiguous silhouette or stalled move.
5. Compare the actual final pose and visible pad/material relationship with the
   contact region before closing. A pose_reached result satisfies the requested
   motion tolerance; it does not certify that both pads reach the chosen material.
   Use a local precision correction only when alignment and clearance support it.

Batch independent queries on a shared snapshot, keeping distinct surfaces in
separate queries when each is needed. Reuse unaffected measurements. After a
slip or contact displacement, refresh only affected geometry. Stop querying
when evidence supports the next bounded action; confirm attachment from the
completed action's observations before dependent transport.

For numeric pad/material overlap or support clearance, read
`general/gripper/grasp-geometry/references/contact-calculation.md`.
Use valid locate points and the published hand geometry with file/code tools.
The reference does not require raw depth access or a separate back_project tool.
