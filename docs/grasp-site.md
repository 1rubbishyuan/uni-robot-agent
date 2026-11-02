# Registered grasp-site control

Environment protocol **3** and tool schema **7** expose one registered grasp site per arm.
Services and clients must agree on these versions and the registered control
frame. Saved trajectories retain the coordinate conventions of their recording.

| Integration | Public site | Model and conversion |
|---|---|---|
| RoboDojo X5 | `x5_grasp_site` | Load native X5 URDF plus `x5_grasp_site.urdf`; FK/DLS end at the site. Native IK receives the inverse fixed transform to link6 exactly once. |
| RoboUse OSC backends | Backend-declared grasp site | Use the native site transform and calibrated OSC conversion declared by the backend. |
| RoboLab DROID | Robotiq `base_link` mounting flange | Convert the world target into the measured robot-root frame; see [RoboLab frames](robolab.md#frames-gripper-and-observations). |

`control_frame`, `grasp_site_description` and `rotation_radius_m` are registered in
`EnvironmentDescription.arms`. The description, including the physical point and
local closing axes, reaches both the agent and Hybrid reviewer. Shared motion
interpolates public site poses without importing robot assets or solving IK.
Rotation budgets remain conservative for displaced native controller points.

`move`, `control_pose`, arrival feedback, FK previews, and reviewer edit/eef
commands describe this same point and orientation. `move` takes six values
[x,y,z,roll,pitch,yaw], with fixed-axis XYZ radians; the primitive protocol,
state and reviewer poses retain wxyz quaternions. To turn in place, keep the
measured XYZ and specify the new absolute orientation. Translation and rotation
interpolate together, so ordered stages require separate completed calls. This is a waypoint/tracking contract,
not a guarantee of exact continuous contact, obstacle clearance, or object attachment.

## X5 definition and geometry evidence

The native X5 URDF has link6 as its controller endpoint but no grasp site. The
extension defines a fixed nominal center between the two distal inner finger-pad
**area centroids**, not the mesh mass center, finger joint origins or fingertips.
The site shares link6 axes: +X toward the fingertips; the fingers close along +/-Y.
The numeric offset has one runtime source: the packaged URDF extension.

Validated asset: `X5A.urdf`, SHA256
`a31a0d3a957196ea42d216036db2fffdf7678f4f36f65f90a0b69c58daa8d7d0`.
The matching `link7.STL` and `link8.STL` distal inward-facing planar surfaces lie
at finger-local Y = -0.024494417 and +0.024494417 m. Area-weighted triangle
centroids, transformed through joint7/joint8 origins, give:

| Quantity | Value in link6 coordinates (metres) |
|---|---|
| Left pad area centroid | [0.149335044815, 0.000401582988, -0.000853957361] |
| Right pad area centroid | [0.149335044980, -0.000405582988, -0.000854017091] |
| Midpoint | [0.149335044898, -0.000002, -0.000853987226] |
| Registered site | [0.149335, -0.000002, -0.000854] |

Each selected face has area 0.000212459419 m². Rounding changes the midpoint by
4.67e-8 m. Opposed prismatic openings of 0, 0.022 and 0.044 m leave this midpoint
unchanged. For 100 sampled arm/root poses, the extended URDF FK agrees with
native-link FK times the fixed transform to machine precision (max 4.45e-16).
This verifies the convention against the current geometry, not real grasp quality;
a different gripper or changed assets require a newly verified definition.

## Preserved boundaries

- RoboDojo actor 14D joint/opening vectors retain their native convention.
- Official RoboDojo return thresholds remain evaluated at native link6. Exact full
  initial site-pose restoration also restores link6, but finite position tolerances
  are not interchangeable when orientation differs.
- General skills describe site/feature relationships. Read the
  [general guidance](../uni_robot_agent/resources/skills/general/) and the
  [RoboDojo geometry and control notes](../uni_robot_agent/resources/skills/robodojo/).
- `UNI_ROBOT_AGENT_SKILLS_DIR` replaces the bundled library. An external library must
  be migrated with the client; old experiments and external copies are not rewritten.


## Metric jaw and calibration tools

Calibrated RoboDojo model tools accept metric jaw openings. The low-level
ArmCommand/JointTarget, VLA and reviewer native vectors retain their normalized
convention. Native closure preload is preserved. Physical pad separation and
effective target gap are exposed in arm metadata; they do not prove attachment.
The runtime robot-reference skill documents metric inputs and camera matrices.
Static robot JSON payloads are published by EnvironmentService before info is
exposed, including prepared/batched hosts; only artifact refs cross that boundary.
