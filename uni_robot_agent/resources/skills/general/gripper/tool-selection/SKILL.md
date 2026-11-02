---
name: tool-selection
description: Choose observation and geometry tools for the next missing fact. Use when planning localization, reusing measurements, or recovering from an uninformative perception query.
---

# Query the information the next action needs

Identify the missing fact before choosing a tool: visible state, object identity,
contact coordinates or a submitted action's execution result. Use existing
evidence when it already answers the question. Query only what changes the next
action; do not precisely map all remaining objects before working on one.
Precision tools are not a prerequisite for a visually grounded VLA action.

## Select the shortest useful query

| Missing information | Tool or evidence | Check before using it |
|---|---|---|
| Did an object fall, remain at its source or visibly move? | Attached images, then view_env_state if needed. | Choose a relevant completed record and camera; segmentation is unnecessary without a geometry question. |
| Where is a feature for the next Direct contact? | capture_scene, then a geometry query. | Capture already returns RGB; a preceding view is unnecessary when the same image answers both questions. |
| Which visible region belongs to one target instance? | locate(method="segment") on the captured snapshot, if exposed. | Check found and overlay identity. A 2D segment alone provides no XYZ; inspect geometry quality only when returned. |
| Where is an already identified local feature using a range image? | locate(method="pixels"), only if exposed. | Use 1–4 interior pixels on the same feature, in [row, col] order; avoid edges, holes and background. |
| Where is a feature jointly visible in calibrated RGB views? | locate(method="triangulate"), if exposed. | Supply the SAME physical point in 2-3 distinct cameras of one snapshot; inspect geometry.valid, reasons and overlays. |
| Has a queued action ended, and how? | wait_for_tool or get_tool_result when available. | Reading an image does not wait for action completion. |

## Reuse the right evidence

Use only the localization methods exposed for this session. A geometric estimate alone
cannot establish feature identity. With RGB-only observations, a segment is a
2D region; use explicit corresponding pixels with triangulate for XYZ only
when camera calibration is enabled and that method is exposed. With calibration
disabled, use visible relations and motion feedback; do not treat pixels as XYZ. Read
`general/gripper/tool-selection/references/rgb-grounding.md` when using it.

Capture the cameras needed for the intended queries. On that snapshot, batch
independent locate calls using available methods whose arguments are already known.
For example, localize the object and its receiver together rather than capturing
again for each. Each query still identifies one instance or local feature;
do not merge unrelated objects into a single coordinate estimate.

For geometry after an action, resolve the relevant physical work before capturing
the new snapshot. Existing snapshots remain readable historical measurements;
independent queries on them do not require waiting for unrelated physical work.
Reuse their coordinates only while the relevant geometry remains unchanged.
For example, after a clear retreat, reuse an untouched receiver's measured
position. Requery only if its pose or relevant geometry is uncertain; do not
capture and localize it solely because the robot moved.

## Examples: decide the independent queries before submitting them

The sequences below are pseudocode, not complete tool payloads. S is an actual
returned snapshot_id; named points and motion targets must already be grounded
in its image or other valid evidence. Cases 1-3 assume range-backed localization is exposed; otherwise use the RGB
reference only if triangulate is exposed, and treat segment as 2D evidence. Use only exposed methods. Lines grouped
as one response are separate tool calls emitted in that same model response.

### Case 1: manipuland and receiver in the same image

Known: S shows a blue block and its destination tray; both positions are needed.
One response: locate(S, head, segment, "blue block") and
locate(S, head, segment, "destination tray").
Inspect both results together, then choose the approach. Do not spend another
model round merely to request the receiver whose query was already known.
If segmentation is unavailable, identify each feature first and use Case 2.

### Case 2: two identified local features using pixels

Known: S shows the solid rim of a held ring and a visible receiver surface;
the relevant interior pixels for each feature have already been identified.
One response: locate(S, wrist, pixels, ring_rim_points) and
locate(S, wrist, pixels, receiver_surface_points).
Keep each call's 1-4 pixels on one feature. Separate results preserve identities;
combining both features into one median is not batching.

### Case 3: a comparison needs both available camera views

Known: S includes head and wrist views, and the next alignment decision needs
independently identifiable features in both images.
One response: locate(S, head, pixels, head_feature_points) and
locate(S, wrist, pixels, wrist_feature_points).
Compare results in world coordinates after both return. Only batch both views
when both are already needed; do not add a second view just to enlarge the batch.

### Case 4: one query fails, the other remains useful

The object query matched the intended object; the receiver query did not.
Retain the valid object result while its geometry remains unchanged. Refine only
the receiver after inspecting its image or overlay. If two independent receiver
features are already visible and both are needed, request them together.
Do not repeat the successful object query or recapture the unchanged scene
merely because one member of the batch failed.

### Case 5: prepare the next target during a known motion

Known: an empty hand has a clear retreat route. S already shows the next object
and receiver, and the retreat will not disturb them or their supports.
One response: move(known_clear_retreat), then locate(S, ..., next_object),
then locate(S, ..., receiver). Inspect localization while the motion runs.
After receiving the job_id, wait when its completed result becomes necessary.
Do not put a blocking wait before these independent queries. Historical geometry
cannot verify the retreat or any object changed by that motion.

### Case 6: calibrated geometry is already required after this action

Known: after a justified verification lift, the next decision requires new
calibrated geometry from head and operating-wrist views regardless of whether
the lift reaches its target. Action RGB images alone cannot supply that geometry.
With ordered submission and capture sharing the physical FIFO, one response:
move(verification_lift), then capture_scene(head, operating_wrist).
The capture executes after the action ends. Inspect the action's completed
feedback as well as its image; retrieve missing feedback using the returned
job_id. Do not queue transport across the still-unverified attachment boundary.
If only visual verification is needed, use the action's head and participating
wrist images instead of adding a capture. Receive the capture snapshot_id before
issuing locate calls that depend on its geometry.

### Case 7: a genuine dependency needs another response

Known: the new image must first be inspected to choose a pixel refinement.
Receive the capture before choosing those pixels. A locate cannot use an ID
that has not returned, and guessing a latest snapshot does not resolve this.
Likewise, if a segmentation overlay determines the refinement point, inspect it
before submitting the refinement. Batch any other queries whose inputs are
already known, but keep this dependent query in the next response.

## Stop measuring when the next action is determined

Choose precision from the physical clearance and observed response. A smaller
tracking tolerance cannot correct a wrong feature, noisy depth or an offset load.
Once evidence supports the next bounded action, take it; a visibly confirmed
attachment needs no additional segmentation just to obtain numeric proof.
If measurements disagree enough to change the action, check the selected feature
and viewpoint, or choose a compatible short probe. Do not average different
surfaces or keep sampling the same ambiguous view to manufacture agreement.

## Change the query that failed

- Missing/wrong target: revise the selected feature or view; when segmentation is
  exposed, refine its instance description or visible point.
- Invalid geometry: inspect the fields of the chosen method. Triangulate returns
  geometry.valid and reasons; change correspondences or viewpoints when invalid.
  Range-backed segment returns geometry_valid and geometry_quality.reasons;
  pixels returns point validity. RGB-only segment makes no 3D claim. Missing
  calibration requires a calibrated capture only when that capability is enabled.
  If calibration is disabled, more captures cannot supply metric geometry.
- Valid coordinates but ineffective contact: check material and finger access.

If refinement remains uninformative, change the view or feature.
Valid depth does not establish identity or certify a feasible grasp; keep
unsupported causes uncertain.

## Example: the mask center lies in a hole

A ring is segmented correctly, but its center projects onto the support through
the opening. Do not keep grasping that center. Select visible solid material,
check access for both fingers and localize that region. Use
`general/gripper/grasp-geometry/SKILL.md` to choose compatible contact.
If the only question is whether the ring moved with the hand, compare images
using `general/gripper/state-verification/SKILL.md` instead of another mask.

## Distinguish a measured surface from a constructed target

Identify the intended feature, inspect its overlay, then check each returned
point's surface identity before using aggregate geometry. Valid depth can be
support, finger or background depth. A batch median does not repair that mix.
A visible surface point is evidence for contact planning, not automatically a
finger-center target: keep the inferred target and its clearance assumptions
separate from the measurement. For range-backed surface ambiguity, read
`general/gripper/tool-selection/references/depth-grounding.md`; for corresponding
RGB features, use the RGB grounding reference.

Recorded RGB observations and action images include local image paths, but camera calibration can be unavailable. Use `capture_scene` for a new snapshot; projection calibration is included only when enabled. Never pair historical RGB with a current wrist transform.
Read the current environment's robot description and published manual for
units, frames and capability limits.

## Keep dependent stages separate

`move` interpolates position and orientation together. A lift required for
clearance and the subsequent turn must be separate calls. Inspect the completed
lift result before submitting the turn; FIFO ordering alone does not verify the
prerequisite. Independent reads against an existing snapshot may still overlap
motion. Target-list order coordinates arms, not sequential waypoints.
