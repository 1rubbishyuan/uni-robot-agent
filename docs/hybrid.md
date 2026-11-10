# Hybrid actor collaboration

Hybrid is an independent tool family. The planner calls `actor_act` with a
language instruction; an actor proposes numeric actions or a symbolic instruction,
and the integration binding translates it into executable samples. An optional
reviewer selects a bounded prefix, stops, or uses a correction capability exposed
by the integration. Every next proposal uses a fresh observation, including after
Direct motion. A discarded suffix is never replayed. Disabling review with
`--no-vla-review` preserves this planner-driven collaboration and execution path.

## Tool composition

Select `--tool-profile hybrid` on an integration that declares it; binding
admission depends on the actual controller and actor profile. The
`--action-profile hybrid` alias remains supported. RoboDojo, RoboUse and RoboLab
can compose Hybrid with Direct tools. DexJoCo, BEHAVIOR, IsaacLab Arena and NaVILA
compose Hybrid with scene, skills and episode tools, without gripper tools.
Integrations own these compositions; a backend never chooses the visible tools.

The Hybrid module creates actor/reviewer clients through session ownership.
Direct does not instantiate an empty Hybrid provider. Explicit visibility
debugging can still declare `actor_act` under Direct, but invocation reports
unavailability and never starts an actor service. Execution return modes and
episode resource exclusion remain independent of the selected profile.

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

Actor v3 declares a numeric or symbolic profile. Numeric profiles retain
independent ordered observation/action fields, action horizon and control
frequency. Symbolic profiles declare allowed labels and each label's parameters.
Each scalar has a name, unit and semantic meaning, with optional frame, control
point, delta reference and bounds. Cameras and image conventions are explicit.
The backend maps this contract to native model inputs/outputs; the integration
validates its physical meaning against the actual environment.

`NumericChunk` carries the complete action matrix; `SymbolicPrediction` carries
one declared label and typed parameters. Both can retain inference evidence.
`ObservationInputs` supplies snapshot state, images and optional camera history.
History requirements declare frame-count bounds and episode/recent scope. Native
sensors own sampling and retain frame IDs, simulation times and state versions;
the backend owns model-specific selection and padding. Capturing only when
`actor_act` runs cannot substitute for intermediate sensor history.

| Binding | Supported contract | Preview and review |
|---|---|---|
| `robodojo_pi05` | Pinned legacy X5 Pi0.5 model/config/RNG/image transforms; explicit clipping | Native FK; bounded pose corrections |
| `robodojo_x5_joint` | Dual X5 absolute joints and continuous normalized openings, 25 Hz | Native FK; bounded pose corrections |
| `robolab_droid_joint` | Seven Panda joints; observed finger-angle fraction, commanded normalized physical gap | Requested and nominal slew-limited flange FK; bounded corrections |
| `robouse_cartesian` | Admitted LIBERO full-pose or RoboCasa fixed-orientation public world control points; absolute poses or latest-ACK-relative deltas | Absolute target previews are nominal requests; deltas do not advertise predicted future motion |
| `dexjoco_tcp_hand` | Absolute TCP pose and named dexterous finger positions | Student/stop; native clipping recorded |
| `behavior_r1pro_joint` | R1Pro torso/arm/gripper positions and local base velocity; OpenPI Pi0.5 | Student/stop; atomic 30 Hz control |
| `arena_g1_locomanipulation` | G1 arm/hand joints, heading-frame velocity and pelvis height; GR00T | Student/stop; native whole-body controller at 50 Hz |
| `navila_go2` | NaVILA front-camera history and discrete navigation predictions | Student/stop; one bounded locomotion segment or yield to planner |

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
   compatibility; implement `encode_observation(ObservationInputs, instruction)`
   and `decode_prediction(prediction, manifest, images, scope)`; validate the full
   proposal before execution and compile each sample against the latest ACK.
   A non-motion prediction can return `YieldToAgent` without issuing a command.
3. Compose reusable adapters/corrections where they fit, or implement
   `HybridBinding` for another command family. Declare strict scope/constraint
   schemas and only the correction capabilities that the device supports.
4. Pass that factory to `configure_hybrid` in the integration's session assembly.
   Declare its Hybrid tool composition and resource policy alongside other tools.
   No change to the common runner, executor or actor backend is needed when
   existing service contracts represent the device and model. A new model recipe
   can supply checkpoint/configuration mappings to an existing backend.

For a dexterous hand, the binding can encode available joint/sensor state and
compile device commands through `/device-steps`, as DexJoCo does. For mobile
robots, `CoordinatedControlStep` combines named radian/metre joint targets, body
velocities and height references in one acknowledged step. The native controller
owns balance and contact handling. Neither path requires scalar gripper tools.
If a device needs a new command or a model needs a new wire protocol, extend that
owning service boundary; do not hide it inside Hybrid.

## Migration and validation

Actor HTTP protocol is now `uni_robot_agent.actor.v3`. Upgrade clients and actor
services together, restart services and open fresh sessions; pre-upgrade session
IDs cannot be reused. Replies carry a typed `prediction`; numeric Python clients
retain the `reply.actions` view. Existing numeric profile semantics and
`actor_act` arguments remain unchanged. New mobile environments advertise
`devices-2` for coordinated control; existing `devices-1`, scalar protocol 4 and
tool schema 8 remain supported. Snapshots retain schema 2 with optional history.

For older Hybrid configurations, replace `active_arms` with `active_groups`.
Opening overrides live under `constraints.gripper_openings_m` when metric
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

The BEHAVIOR R1Pro, Arena G1 and NaVILA Go2 adapters have CPU contract checks.
A Qwen + Pi0.5 R1Pro run executed 3,024 native control steps; the official task
result was unsuccessful and the original run had a finalization error. A separate
native check verified the subsequent finalization fix without changing that result.
A Qwen + GR00T G1 run completed four `actor_act` calls, 21 predictions and 1,003
native control steps with the original task evaluator reporting success. Before
that run, aligning the cold spawn with the upstream seeded reset removed the
box-motion anomaly; three seeds retained the original physics and reset poses.
This is a single successful episode, not a success-rate estimate or an additional
post-release settling check. A Qwen + NaVILA Go2 run completed seven `actor_act`
calls, 23 movement predictions and 1,575 native control steps, then explicitly
called `finish`. The native evaluator reported success with distance to goal
1.387 m and SPL 0.834; finalization and outcome readback completed normally.
This is one episode under the explicit `isaac51` Hybrid profile, not a benchmark
success rate or an evaluation equivalent to the upstream standalone Isaac 4.1 run.

Arena uses the pinned official Isaac Sim 5.1 branch with CPU physics and GPU
rendering. NaVILA retains its pinned `isaac41` profile, which rejects Blackwell
GPUs, and adds an explicitly selected `isaac51` compatibility profile. The latter
pins the source migration and isolates visual material subsets from collision
geometry while preserving mesh vertices, triangles, the locomotion policy and
sensors. A full-scene native check completed 100 warmup and 150 commanded control
steps, travelling 0.849 m; it is not a NaVILA navigation success result.
BEHAVIOR requires separately installed native assets/runtime and task-compatible
weights. The [BEHAVIOR](behavior.md), [Arena](isaaclab_arena.md)
and [NaVILA](navila.md) guides describe setup and remaining acceptance work.
Their examples default to Qwen with review disabled.

## Symbolic navigation

NaVILA predicts forward/turn/stop instructions from RGB history and language.
Its binding compiles movement into a bounded velocity segment; the environment's
Go2 controller executes and captures intermediate sensor frames. Actor STOP
returns `YieldToAgent`: it does not end the episode or claim arrival. The planner
can observe, request another phase or call `finish`. Native private metrics
establish the final outcome. Requested displacement is never reported as measured
travel, and no simulator reference route or exact goal is passed to the model.

## Coordinated dexterous devices

DexJoCo uses `dexjoco_tcp_hand` with the same actor/reviewer runner and bounded
execution loop as other integrations. Its environment publishes arm, hand and
assembly devices. `CoordinatedDeviceStep` carries named TCP poses and named finger
joint targets; the native adapter validates the entire target before advancing
one control step. One acknowledged step completes one model sample.

The binding preserves the model's physical semantics, converts rotation vectors
to quaternions, and explicitly records the native actuator clipping applied to
finger targets. It never substitutes scalar gripper openings or an IK controller.
Capabilities admit student/stop review; Direct and EEF corrections are unavailable.
The first released model recipe and native task are single-arm `pick_bucket`.
Dual-arm layout fixtures do not establish dual-arm native task validation.

Model loading belongs to the generic OpenPI backend; source, training config,
normalizer and image mapping belong to its recipe. A new compatible environment
provides an environment adapter and binding, while reusing the backend and runner.
See [DexJoCo](dexjoco.md) for the reproducible single-task checks.


## Reviewer decision contract and evidence

An enabled reviewer submits exactly one `submit_review` function call. Separate
reasoning is permitted; prose, multiple calls, incomplete responses and malformed
arguments never authorize motion. The function schema describes the current
request ID, action budget and binding correction capabilities. Optional correction
fields remain optional in the provider schema (`strict: false`); local validation
requires exactly the fields for the selected mode and enforces all cross-field
constraints before execution.

`assessment.execution_status=not_started` means that the entire native episode
has not executed an action. It does not mean a new local phase or an unachieved
subgoal. When the binding supplies `episode_started`, the schema exposes only
statuses consistent with that observed history, and local validation checks it.

A rejected decision receives at most one repair request for the same captured
observation, proposal and request ID. Repair never calls the actor again or
executes any part of a rejected proposal. Transport failures retain the existing
bounded transport policy and do not receive this decision-format repair. Two
rejected decisions stop the tool call with `error_stage=reviewer`; already
acknowledged actions from earlier chunks remain in its result.

Each review attempt is archived beside its chunk as `NN.review-A.json` and
`NN.review-A.response`. Metadata contains provider attempt records, timing and
an accepted decision or error; the response file retains original upstream bytes
before provider conversion, with its byte count and SHA-256 in the metadata.
Authorization headers are not included. Failure to persist review evidence stops
before the approved prefix executes. Chunk context and session summaries retain
main tool `call_id`, `batch_id` and `execution_id` for tracing zero-action errors.
