---
name: pose-and-order-memory
description: Keep original target poses, live estimates and ordered events separate across transfers and compaction.
---

# Preserve what the instruction refers to

Record only the state needed by the goal, with its supporting observation:

- Original reference: identity, destination mapping, required position and
  directed orientation or face, order and applicable task tolerances.
- Live estimate: latest observed support or holder and relevant feature geometry.
- Verified progress: completed relationships or events and any later disturbance.
- Unresolved work: uncertain identity, pose, event or pending action result.

Update live estimates after movement without overwriting original references.
For a pose exchange, derive the destination mapping from the instruction; do not
replace original orientations with a convenient canonical orientation. Distinguish
an axis from a directed feature so that a half-turn cannot pass as agreement.
Grasp-site yaw and bounding-box aspect ratio alone do not establish object orientation.

After release, compare the required object features with the original reference
using current observations and the actual task tolerance. An acknowledged
residual outside that tolerance remains unfinished work, even if translation is
accurate. Preserve references, failed corrections and the relevant guidance in
continuation summaries; a summary is memory, not new physical evidence.
