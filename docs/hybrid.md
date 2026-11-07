# Hybrid actor collaboration

Hybrid is an independent tool family. An actor proposes a chunk; an optional
reviewer selects a bounded prefix, stops, or uses a correction capability exposed
by the integration. Every next chunk uses a fresh observation, including after
Direct motion. A discarded suffix is never replayed.

## Responsibilities

| Layer | Owns | Does not own |
|---|---|---|
| `services/actor/` | Model sessions, prediction receipts, explicit profiles and backend wire mapping | Robot execution or integration selection |
| `tools/hybrid/` | Tool schema, capture/inference/review progression, one bounded execution loop, aggregate feedback | Arms, grippers, native drivers or model wire fields |
| `integrations/<name>/action_binding.py` | Profile compatibility, observation encoding, prediction interpretation, capability composition and command compilation | Inference calls or prefix loops |
| `tools/hybrid/adapters/arm_gripper.py` | Optional arm scope, opening constraints and domain command/outcome conversion | Model loading or execution loops |
| `tools/hybrid/corrections/cartesian.py` | Pure bounded pose and rotation geometry | Gripper semantics or physical control |
| Native environment adapter | Sensors, calibration, FK/IK, controller limits, one physical command and authoritative outcome | Reviewer or agent orchestration |
| `integrations/hybrid.py` | Options and actor/reviewer ownership through the prepared session | A central robot or binding registry |

`HybridTools` and Direct motion reserve the same exclusive `episode` resource.
The existing environment client owns command identity and receipt reconciliation.
The hybrid executor acknowledges each returned state immediately, then interprets
its outcome. A later compile, feedback or archive failure cannot erase confirmed
commands. Counts of completed policy samples and acknowledged commands remain
separate from elapsed duration and partial/unknown execution. A timed action may
have executed partly without incrementing a completed-command counter.

## Profiles and admitted bindings

Actor v2 declares independent ordered observation and action fields. Each scalar
has a name, unit and semantic meaning, with optional frame, control point, delta
reference and bounds. Frequency, horizon, cameras and image conventions are also
explicit. The backend maps these fields to model wire keys. The integration
validates their physical meaning against the actual environment.

| Binding | Supported contract | Preview and review |
|---|---|---|
| `robodojo_pi05` | Pinned legacy X5 Pi0.5 model/config/RNG/image transforms; explicit clipping | Native FK; bounded pose corrections |
| `robodojo_x5_joint` | Dual X5 absolute joints and continuous normalized openings, 25 Hz | Native FK; bounded pose corrections |
| `robolab_droid_joint` | Seven Panda joints; observed finger-angle fraction, commanded normalized physical gap | Requested and nominal slew-limited flange FK; bounded corrections |
| `robouse_cartesian` | Admitted LIBERO full-pose or RoboCasa fixed-orientation public world control points; absolute poses or latest-ACK-relative deltas | Absolute target previews are nominal requests; deltas do not advertise predicted future motion |

Generic bindings do not require `xpolicylab_ws`: any backend declaring the exact
compatible profile can serve them. The legacy Pi0.5 path deliberately retains its
pinned model/runtime requirements. XPolicyLab itself has no X5 or integration
assumptions; its configuration supplies complete observation/action wire mappings.
This does not establish checkpoint compatibility or benchmark performance.

RoboUse tasks, scenes, seeds and checkpoints do not each need a binding. They reuse
one when their controller and policy semantics match. Other RoboUse controller
families are not advertised for Hybrid until their capabilities are validated.
RoboCasa actions contain only xyz plus normalized opening (four values);
observations retain measured xyz, wxyz and opening (eight values). It preserves
its native target orientation and rejects profiles containing angular action
fields, rotation commands and rotation corrections.

## Add another integration

1. Implement the native environment contract and expose the measurements,
   command semantics and actual outcomes needed by the policy. Reuse the existing
   episode, snapshots and command receipts.
2. Add a pure binding factory with signature
   `create_binding(name, args, description, actor_info)`. Validate complete profile
   compatibility; encode captured observations; decode and validate the entire
   proposal before execution; compile individual rows against the latest ACK.
3. Compose reusable adapters/corrections where they fit, or implement
   `HybridBinding` for another command family. Declare strict scope/constraint
   schemas and only the correction capabilities that the device supports.
4. Pass that factory to `configure_hybrid` in the integration's session assembly.
   Declare the hybrid tool name and exclusive resource policy alongside its other
   tools. No change to the common runner, executor or actor backend is needed when
   existing service contracts can represent the device and model.

For a dexterous hand, the binding may encode joint/tactile state and compile timed
`JointSegment` commands through `/device-steps`. It need not expose arms or scalar
grippers. If the device needs a new native command or the model needs a new wire
protocol, extend that owning service boundary; do not hide it inside Hybrid.
This change includes a CPU device fixture, not a production hand driver or WAM.

## Migration and validation

Upgrade matching clients and services together: actor protocol v1 becomes v2,
scalar environment protocol 3 becomes 4, and its tool schema 7 becomes 8. The
separate device protocol is unchanged. Replace `active_arms` with `active_groups`.
Opening overrides now live under `constraints.gripper_openings_m` when metric
calibration exists, or `constraints.gripper_targets` for normalized openings.
These fields are optional adapter capabilities, not universal Hybrid parameters.

```json
{
  "instruction": "Move the block toward the tray",
  "active_groups": ["right"],
  "actions_per_chunk": 10,
  "max_chunks": 1,
  "constraints": {}
}
```

Reviewer modes `student` and `stop` are generic. `edit` and `eef` appear only when
the binding supports them. The included arm adapter caps a prefix at
`min(horizon, 40)` and a correction at five commands, with 0.05 m / 0.35 rad
geometry bounds. Absolute correction targets are fixed at the review boundary;
recompilation cannot accumulate the same offset on later measured states.

Keep a selected checkpoint's training semantics, normalization, camera identity,
control point and timing consistent with the declared profile. CPU fixtures verify
contracts, receipts, ordering and conversion. They do not establish physical
safety, task success, or pretrained VLA quality on new environments.
