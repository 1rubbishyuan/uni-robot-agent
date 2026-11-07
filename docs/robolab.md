# RoboLab

`--integration robolab` connects a prepared, single-DROID RoboLab episode
to the shared scene, gripper, skill and finish tools. The native adapter supports
RoboLab `0.3.1` at revision `ad45d4f974725d020f82c2b0d77d78533aeba2b3`.
See [source provenance](../uni_robot_agent/integrations/robolab/SOURCE.md).

## Installation and native preparation

Keep the planner and simulator in separate Python environments. Follow the pinned
[upstream installation](https://github.com/NVlabs/RoboLab/tree/ad45d4f974725d020f82c2b0d77d78533aeba2b3)
to prepare RoboLab, its supported Isaac Sim/Isaac Lab stack, benchmark task library
and all referenced scene/robot assets. Materialize Git LFS assets; placeholder
pointer files are not usable USDs. Isaac requires a compatible NVIDIA RTX GPU.
The validated native runtime is Isaac Sim 5.1 / Isaac Lab 2.3.2.post1.

From the repository root, install the agent and geometry dependencies into the
simulator’s Python environment. Isaac supplies the USD bindings; do not replace
them with a separate `usd-core` wheel in the native environment.

```bash
SIM_PYTHON=/absolute/path/to/prepared/robolab/python
"$SIM_PYTHON" -m pip install -e '.[robolab]'
```

The planner needs the normal harness installation and provider configuration. It
does not need RoboLab, Isaac, Torch, USD or FCL. Recording additionally requires
`ffmpeg` in the native service's PATH.

## Prepare and serve an episode

The output directory must be new. The service prepares and resets one native
episode before publishing `/info`. Clients never reset it. Restart the service
with a new output directory for another task or seed.

```bash
export SIM_PYTHON=/absolute/path/to/prepared/robolab/python
bash scripts/deploy/services/robolab/start_environment.sh \
  --task BananaInBowlTask --seed 0 --instruction-type default \
  --headless --device cuda:0 --host 127.0.0.1 --port 18770 \
  --record-video --output-dir /absolute/path/to/new-run/environment
```

Native operations, including reset, stepping, calibration, capture and shutdown,
run on Isaac's main owner thread. The shared ASGI service uses a background
thread; shutdown drains in-flight requests while the owner queue keeps running.
Scene reads and capture consume no native physics ticks. One accepted primitive
executes exactly one native control tick.

The gripper torque limit defaults to the reference asset's 16.5 Nm and is checked
against PhysX readback. `--gripper-torque-limit-nm` changes it explicitly and is
recorded in metadata. `--compact-gpu-buffers` opts into
smaller per-process allocations without changing native timesteps or solver
settings; check native capacity warnings before using it with new scenes.

## Connect a planner

An offline configuration check requires no simulator or model call:

```bash
uni-robot-agent run --integration robolab --task BananaInBowlTask --seed 0 \
  --model YOUR_MODEL --env-url http://127.0.0.1:18770 \
  --action-profile direct --no-sam3 --validate-config
```

After checking the live service's task, seed, instruction type and RGB cameras,
use the same arguments for a run with the normal provider options:

```bash
uni-robot-agent run --integration robolab --task BananaInBowlTask --seed 0 \
  --model YOUR_MODEL --env-url http://127.0.0.1:18770 \
  --action-profile direct --no-sam3 --reasoning-effort max \
  --episode-timeout-s 10800 --max-turns 0 \
  --output-dir /absolute/path/to/new-run/agent
```

Use the provider's existing credential loader; do not put keys in commands or
recordings. This integration disables automatic episode retry. A lost action
response is reconciled using the existing shared receipt mechanism.

## Frames, gripper and observations

- `right` controls the Robotiq `base_link` mounting flange. It is not the
  fingertip midpoint or the rotated `eef_frame`. Public poses use world metres
  and internal wxyz quaternions; `move` accepts fixed-axis XYZ radians. The
  adapter converts the world pose to the measured robot-root frame once.
- Tools expose metric `gripper_opening_m` / `opening_m` through the existing
  optional opening calibration. The normalized low-level value represents
  requested pad gap / 0.085. The adapter inverts the nonlinear USD linkage to
  obtain the native finger-joint action. Requests below the reachable gap
  saturate at native travel; actual pad separation stays separately measured.
- Full drive intent persists when contact blocks the jaws. The applied
  slew-limited target and measured gap are distinct state metadata. Joint
  commands advance at no more than 1 rad/s of commanded slew per native tick.
- Per-step IK, joint-limit and conservative robot-only swept-clearance checks
  run before physics. They do not check task objects or scene obstacles and do
  not establish grasp success. Monotonic escape from an initial robot-clearance
  violation is permitted.
- Native RGB is capped at 1280 pixels on the longest edge. Intrinsics are scaled
  to that exact grid. Optical transforms use +x right, +y down, +z forward;
  moving camera poses update with images, including after reset. No depth is
  advertised. Shared multi-camera triangulation is available when two or more
  calibrated views exist; the model must identify corresponding physical points.
- Only sensor images and measured robot state are public. Robot-only geometry
  and drive parameters use the shared artifact store and scene reference. Native
  task predicates, object ground truth, HDF5 recordings and results are not
  included in observations or tool feedback.

## Outcomes and evidence

RoboLab's `all_terminated` freeze ends action execution; transient termination
tensors alone do not. Native `get_env_results()` determines success regardless
of the model's completion claim or the reason the agent stopped. An unknown
native result remains `null` in `native_result`; an incomplete evaluation is
recorded separately as unsuccessful with `failure_category=episode_incomplete`.
Native action faults are latched and classified as unaccepted environment errors.
Finalization and native recording flush run once; a failed flush is not replayed.
If the result request fails, the session records an unaccepted, nonretryable
`robolab_result_unavailable` outcome with success `null`; it does not fabricate
a task verdict or resubmit native finalization.

The environment writes `config.json`, shared `artifacts/`, and native evidence
under `native/`, including `native_outcome.json`. RoboLab owns its own native
configuration and HDF5 recordings. With `--record-video`, `native/episode.mp4`
and `native/video.json` contain reset plus each executed tick, timed by native
simulation time. The planner saves its usual trajectory and outcome copy.

## Implementation boundaries

Robot geometry, FK/IK, jaw calibration and drive feedback live in this adapter.
The agent uses shared motion and scene tools, with one environment service,
artifact store and episode state. Native task predicates, step limits and arm
gains remain owned by RoboLab. Controller convergence does not establish task
success.

## Validation

The single-DROID native integration has been validated on the runtime above.
Release checks also cover imports and configuration. For a new installation,
verify task loading, RGB/calibration, bounded motion, recording and native
outcomes with your assets and runtime.

When changing this adapter, check frame conversion, nonlinear jaw calibration,
blocked-grasp command retention, owner-thread dispatch, camera refresh,
one-time finalization, fault latching and video timestamps.

## Hybrid joint policies

Use [the Hybrid example](../examples/robolab_hybrid.sh) with a separately deployed
actor and its checkpoint SHA256. `robolab_droid_joint` validates seven Panda joints,
the Robotiq mounting-flange control point, actor cameras and the actual environment
frequency. Model observations use the measured finger joint divided by pi/4;
action openings use physical pad gap divided by 0.085 m. These are distinct
nonlinear conventions, not a `1 - x` conversion.

The native preview runs FK on a controller snapshot and reports both requested
and nominal slew-limited targets. It advances no physics and does not change live
controller history. Later nominal samples assume perfect tracking; preview is not
object, contact or collision prediction. The real controller still validates
measured state, limits and robot clearance before each acknowledged tick.
No pretrained checkpoint or native benchmark result is implied by this binding.
