---
name: shape-based-grasping
description: Choose grasp candidates from visible local shape, opposing finger access and support clearance, especially when a center pinch fails.
---

# Select grasp candidates from local shape

Use the local contact geometry, not the object's identity, to select a candidate.
An object can present several of the features below; compare the accessible
regions for the next operation rather than assigning one grasp to its whole shape.
A candidate remains conditional on the current support, hand geometry and reach.
Shape alone does not establish friction, rigidity, load balance or secure retention.

## Elongated regions and changing cross-sections

When a local section has room for both pads, consider closing across that section.
Inspect its width, taper and nearby changes in thickness rather than targeting
the whole-object mask center. Select a region the fingers can enclose with
useful pad overlap, leaving the required working or transfer region accessible.

A junction or thicker section is another candidate only if both finger paths
and the loaded route remain clear. Its presence does not establish an axial stop
or guarantee retention. Check clearance for the entire object: raising the hand
does not prove that a distant end has left the support. After displacement,
refresh the affected section before repeating the approach.

## Low bodies with a sloping upper surface

If upper-slope contact slips or pushes the object during closure, compare lower
opposing side regions where the pads can enclose material. Resolve both sides
and the support; hand inclination can leave one pad above the intended region.

Lower contact is useful only while both fingers clear the support and can reach
opposing material. Compare actual arrival and pad overlap before lowering again.
A stalled descent or upward displacement does not establish a correct contact
height; inspect the obstruction instead of descending until something stops.

## Rounded or irregular bodies

Compare both approaching finger paths with the current outline and support.
A near-tangent approach may displace the body before the other pad encloses it.
Consider another exposed region or approach that brings the pads to opposing
sides without an earlier sweep through the object.

A narrow tip or projection has different contact area and loading from the body;
compare its geometry rather than assuming it offers the same hold. If brushing
moves the object, update the affected contact region before closing. A small
measured opening alone does not distinguish retention from empty closure.

## Thin walls, openings and exposed edges

Choose observed solid material. Where the opening and full entry path admit a
finger, consider one pad inside and the other outside a wall or exposed edge.
Compare this with accessible opposing outer contacts when the gripper span fits.
The hole center describes access, not a solid contact point.

Check the space for each finger at the selected height, including finger
thickness, support and neighboring material. For an edge pinch, also check the
hanging body's clearance and whether the grip obstructs its next use. Withdraw
before a lateral correction when sliding would drag the object. After slip,
reconsider the region, closing direction or permitted supported regrasp; no
fixed inclination applies to every opening or edge.

## Shallow pieces with branches, concavities or holes

Compare an exposed local segment away from a junction, a wall with adequate
inner access, or two exposed ends within the gripper span. The whole-shape center
may lie in a void or put one finger above a neighboring branch. Neither always
closing across nor always closing along the long direction resolves this.

Inspect both finger entry paths, not only the target center. When another branch
blocks a finger, change the location along the segment or the closing direction;
a lower target at the same obstruction may push the piece instead of enclosing it.
For shallow material, resolve the support separately and compare the actual pad
height with the material. Valid depth through a hole can belong to the support;
do not average those samples with the intended contact surface.

## Accessible opposing planar faces

Compare face contact with a shallow corner or top-edge pinch. Align closure with
the observed opposing faces and choose sufficient pad overlap while preserving
support clearance. A coarse arrival tolerance can leave less overlap than the
requested pose suggests; use the actual final pose to assess this margin.

Check the loaded approach and withdrawal as well as pickup. If contact disturbs
another supported object, refresh both affected relations before retrying.
Slower transport does not compensate for missing opposing contact or blocked
clearance.

## Evaluate the selected candidate

Construct coordinates and orientation from current observations and calibrated
hand geometry. Read `general/gripper/grasp-geometry/references/contact-calculation.md`
when pad overlap or support clearance needs a numeric check; use
`general/gripper/tool-selection/references/depth-grounding.md` for uncertain
surface identity. A visible surface point does not reveal an unseen underside.

Use the completed action's images to assess the intended object's relation to
the hand and support. When retention remains unresolved, choose a bounded check
compatible with the grip and next motion; a lift does not establish retention
through a later rotation. Apply `general/gripper/state-verification/SKILL.md`
for that decision. Reuse unaffected measurements and batch already-known route
segments where execution dependencies permit; refresh displaced geometry rather
than measuring the whole object again after every segment.
