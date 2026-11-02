---
name: general-index
description: Index of shared manipulation skills selected by available capabilities, observed geometry, operation and unresolved evidence.
---

# General manipulation skill index

The common and gripper skills contain general principles. Check the current
robot description and tool schemas for available arms, calibration and feedback.
Environment-specific manuals supplement these principles without changing the
task instruction. Two-hand and calibrated-geometry skills have explicit capability
requirements. References under
`gripper/` expand those principles without prescribing object-specific recipes.
Shape-based grasp guidance proposes candidates from visible local geometry,
finger access and support clearance; it does not prescribe a grasp by object name.

Read the common skill first. Use read_skill with a path below when its condition
applies; do not read the whole library by default or reread unchanged guidance
per action. Derive assignments, quantities, order and final relationships from
the current instruction and observations.

This is a curated operation guide. To discover all currently available Markdown
files, including later additions, use list_skills without arguments. It
returns shared general/ paths and the current environment namespace in entries;
pass a relevant file path to read_skill for its full content. Discovery includes
every skill subdirectory in those two scopes.

| Path | Read when |
|---|---|
| `general/SKILL.md` | Planning both hands' roles, common evidence, batch boundaries and recovery |
| `general/gripper/handover/SKILL.md` | Transferring support from one hand to the other |
| `general/gripper/tool-selection/SKILL.md` | Choosing observation versus geometry queries, batching independent measurements, reusing evidence or revising failed localization |
| `general/gripper/pose-targeting/SKILL.md` | Registered grasp-site targets, rotation semantics or held-feature alignment |
| `general/gripper/return-to-start/SKILL.md` | Returning an empty arm to its initial pose or verifying the finishing posture |
| `general/gripper/motion-recovery/SKILL.md` | Difficult reorientation; distinguishing IK failure, joint-transition rejection and contact stalls before changing route |
| `general/gripper/state-verification/SKILL.md` | A dependent action needs an unresolved state, or completion depends on identities and prior evidence |
| `general/gripper/contact-effect/SKILL.md` | Contact is new, ineffective or ambiguous, or its grip, working geometry or support has changed |
| `general/gripper/grasp-geometry/SKILL.md` | Choosing contact; a reached pose still yields an empty grasp, or a verified lift slips during carry |
| `general/gripper/placement-and-engagement/SKILL.md` | Choosing receiver restraint or repositioning, establishing containment, and advancing engagement to seating |
| `general/gripper/supported-contact/SKILL.md` | Both objects need control, or a held load needs the free hand to adjust or restrain its receiver |
| `general/gripper/scene-memory/SKILL.md` | Remembering identities and event order, or distinguishing demonstration recognition from the execution handoff |
| `general/gripper/moving-target-control/SKILL.md` | Target or receiver motion changes approach, capture timing or the transfer route |
| `general/gripper/held-object-geometry/SKILL.md` | Clearance, entry or working contact depends on the held object's actual pose and features |
| `general/gripper/reachability-and-recovery/SKILL.md` | Checking destination entry before pickup, choosing a supported relay, or reassigning a failed route |
| `general/gripper/deformable-material/SKILL.md` | Flexible material changes shape and requires region tracking and a released-shape check |
| `general/gripper/contents-transfer/SKILL.md` | Transferring loose contents through a vessel opening |

Read another reference when a changed operation or unresolved question makes it
useful. Guidance does not establish physical state; use observations and still-valid
process evidence. A remembered procedure adds no requirements to the instruction.

## Optional general references

Read a matching reference only when it resolves a missing fact or a failed
hypothesis; reuse it once in context. Match guidance to observed local geometry,
support, finger access and next operation, then evaluate it in the current scene.

| Path | Read when |
|---|---|
| `general/gripper/tool-selection/references/depth-grounding.md` | Range-backed localization is exposed and surface points are ambiguous |
| `general/gripper/tool-selection/references/rgb-grounding.md` | Matching a feature across RGB views for triangulate, or interpreting its geometric checks |
| `general/gripper/contact-effect/references/exact-count-cycles.md` | Calibration or ambiguous contact may already have contributed to an exact event count |
| `general/gripper/scene-memory/references/pose-and-order-memory.md` | Original poses, directed orientation or event order must survive movement or compaction |
| `general/gripper/grasp-geometry/references/contact-calculation.md` | Computing pad contact, finger/support clearance or candidate poses from calibrated geometry |
| `general/gripper/grasp-geometry/references/shape-based-grasping.md` | Comparing grasp candidates for visible cross-sections, curved surfaces, openings, branches or opposing faces with current finger and support clearance |
