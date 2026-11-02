# RoboUse gripper integration (experimental)

This integration connects RoboUse 0.4.1 native episodes to uni-robot-agent's existing
prepared-episode service and gripper/scene tools. It reuses the shared tools,
session lifecycle and motion loop. In-process simulators use one owner thread;
worker simulators use a dedicated
child process per episode, including camera capture and teardown.

## Admission, not a runtime success claim

The bundled 0.4.1 task inventory admits 390 bundled configurations; another 20 GPU task IDs have adapters but
require external task folders:

| Environment script | Admitted configurations | Control |
| --- | ---: | --- |
| `run_tabletop.sh` | 86 | Translation, absolute finger aperture |
| `run_metaworld.sh` | 74 | Translation, aperture error to native finger effort |
| `run_fetch.sh` | 6 | Fixed-base Fetch, finger position deltas; locked fingers remain closed |
| `run_kitchen.sh` | 10 | Translation and world Y roll |
| `run_libero.sh` | 130 | Panda world-frame OSC pose |
| `run_robosuite.sh` | 11 | Panda OSC; one or two robots |
| `run_menagerie.sh` | 12 | Fixed single/dual arms; task-defined yaw constraint |
| `run_components.sh` | 25 | Composed fixed Panda/ALOHA, translation |
| `run_maniskill.sh` | 12 bundled | CPU native worker, commanded TCP pose |
| `run_robocasa.sh` | 24 bundled | Fixed base/torso; world translation |
| `run_maniskill_gpu.sh` | 12 native IDs; task folders not bundled | Native delta-pose controller |
| `run_genesis.sh` | 3 native IDs; task folders not bundled | World TCP pose; binary gripper |
| `run_factory.sh` | 5 native IDs; task folders not bundled | Upright pose, native EMA, pregrasped part |

These are task configurations, not distinct environments or verified episodes.
Inventory is reproducible without simulation:

```bash
"$ROBOUSE_PYTHON" scripts/robouse/inventory.py > /persistent/path/robouse-inventory.json
```

Admission rejects mobile/dexterous embodiments, maze tasks, RoboDojo (already
integrated), CaP-X/LIBERO-PRO, RLE-Bench, RoboHarm and other unimplemented backends.
It also rejects reference-image Tabletop tasks
(5), refusal Tabletop tasks (4), Menagerie switch-arm tasks (3), unsupported
RoboSuite robots/grippers (10), and composed non-Panda/ALOHA (20) or
safety/text-submission tasks (3). Six GymRobotics maze configurations are excluded.
GPU Playground tasks and arbitrary GPU `opts` remain excluded.
No text answer or refusal is inferred from a summary or an agent stopping.

Custom task folders use the same admission checks, but the inventory and source
compatibility review apply only to the bundled pinned release. A startup error
is not a supported-task result.

## Dependencies and assets

Use Python 3.12 and new persistent environments. RoboSuite 1.5.2 and LIBERO's
RoboSuite 1.4.0 must not share an environment. `setup.sh` refuses an existing path:

```bash
bash scripts/robouse/setup.sh mujoco /persistent/env/robouse-mujoco /path/to/python3.12
bash scripts/robouse/setup.sh libero /persistent/env/robouse-libero /path/to/python3.12
```

This installs environment-service dependencies; use the existing harness
interpreter for the agent SDK. Package requirements do not provision GPU drivers,
EGL/OSMesa system libraries, or external model assets. Before a real run, set
`MUJOCO_GL=egl` on a reserved GPU host. Do not set it to `disable` for simulation;
that option is only for import validation. Set `LIBERO_CONFIG_PATH` to a persistent
configuration directory, and `ROBOUSE_CACHE_DIR` to a persistent cache directory.

Menagerie and composed Panda/ALOHA additionally need upstream pinned model assets:

```bash
export ROBOUSE_CACHE_DIR=/persistent/cache/robouse
"$(dirname "$ROBOUSE_PYTHON")/robouse" fetch-assets --robot franka_emika_panda --robot aloha
```

Verify this command against `robouse fetch-assets --help` when provisioning; no
asset download or simulator launch is performed by the server's check-only mode.
The RoboUse wheel includes task definitions; it does not include these meshes.

For CLI-managed preparation and service ownership, use the
[deployment guide](../../docs/deployment.md#environment-profiles-and-limits).
Runtime setup is separate from native task validation: check reset, rendering,
motion and finalization with the selected profile before attempting a task.

## Launch choices belong to scripts

Each environment script states its cameras and defaults to no depth. Override
with `--depth`, `--no-depth`, or `--cameras ...`. No backend auto-enables SAM or
selects a perception policy. The agent command requires an explicit `--no-sam3`
or `--sam3-url URL` (or the existing harness environment setting).

Example, from the repository root, after dependency/asset preparation:

```bash
export ROBOUSE_PYTHON=/persistent/env/robouse-mujoco/bin/python
TASK_ROOT=$("$ROBOUSE_PYTHON" -c 'from robouse.tasks import bundled_tasks_root; print(bundled_tasks_root())')

# Does not create an episode, renderer, output directory, or service.
bash scripts/robouse/run_tabletop.sh \
  --task-dir "$TASK_ROOT/arc-style/arc-colour-to-pad" \
  --output-dir "$PWD/eval_outputs/robouse-tabletop-smoke/environment" \
  --run-id robouse-tabletop-smoke --check-only

# On a reserved GPU, omit --check-only to start the service in this terminal.
# Use a fresh output directory.
# In another terminal, choose the agent/model/provider as in existing runs:
export HARNESS_PYTHON=/path/to/existing/harness/bin/python
export ENV_URL=http://127.0.0.1:18780
bash scripts/robouse/run_agent.sh --model YOUR_MODEL --no-sam3 \
  --output-dir "$PWD/eval_outputs/robouse-tabletop-smoke/agent"
```

The agent script defaults to direct control, 10800 seconds and unlimited turns;
CLI arguments can override the budgets. The native task's step budget remains
unchanged. The upstream wall-time budget is recorded in `config.json`, not
implicitly applied over the script's explicit harness timeout.

## Observations and grading

Model observations contain robot proprioception and configured images, optionally
metric depth. They do not contain native object poses, contact labels, reward or
success flags. Camera K and optical-to-world matrices come from the actual renderer
camera even with depth disabled, allowing existing RGB geometry tools to use them.
Multi-camera captures contain no intervening physics step. In-process images and RoboCasa capture use 320px square resolution; other worker
cameras keep their native image dimensions. High-resolution capture does not
change those resolutions.

This is a declared `uni-robot-agent-rgb-proprio` evaluation profile, not an unchanged
RoboUse leaderboard observation protocol: camera availability, calibration,
privileged state access and wall-time budgets can differ. No-depth does not mean
uncalibrated cameras. Upstream task notes are preserved after removing exact CLI
boilerplate; the prompt states that native CLI commands are unavailable.

Every accepted primitive calls `Episode.act(..., repeat=1)`. RoboUse retains its
native step budget, first-success termination and final physical grader. Normal
completion maps to native `done`; give-up and shutdown cannot fabricate success.
The original final settling steps (10, outside the native agent-step count) remain.
Native JSON verdicts and video live under `environment/native`; harness traces and
artifacts use the normal service path. Native RPC request counts/logs are not
reproduced because the integration calls Episode directly; harness traces provide
the action audit. Special text/refusal submission protocols remain unsupported.

## Validate your installation

`--check-only` checks task admission and service configuration without creating,
rendering or stepping a native episode. The agent's `--validate-config` checks
planner arguments without calling a model. Neither verifies a native runtime.

For each backend you intend to use, verify reset, RGB/depth calibration, bounded
motion, gripper behavior, recording and finalization with the prepared native
runtime. Then run a complete graded task. MetaWorld aperture effort and native
controller tracking require simulation evidence; configuration checks alone do
not establish correct motion or task success.

## Worker extension and launch

The additional backends use RoboUse's public `robouse.backends` entry point,
registered by the uni-robot-agent package. The server preserves the task ID, seed,
step budget and success mode while dispatching the native Episode to
`uni_robot_agent_worker`; the original backend is retained as metadata. This avoids
patching either uni-robot-agent or installed RoboUse. Physics, reset, hooks/perturbations,
scoring and final settling still come from RoboUse's pinned native workers.
The bridge uses local stdio child processes on the selected host; it does not
connect to a pre-existing upstream GPU HTTP worker pool.

Install the entry point in the environment-server interpreter (new `setup.sh`
environments do this automatically):

```bash
"$ROBOUSE_PYTHON" -m pip install .
```

Set `ROBOUSE_WORKER_PYTHON` to the interpreter containing that simulator's runtime.
Install RoboUse 0.4.1 and the matching uni-robot-agent build into that interpreter,
alongside the native dependencies. Workers clear `PYTHONPATH` and disable user
site packages, so the server's source-checkout path does not supply worker imports.
From the repository root, install the harness into an independently prepared worker:

```bash
"$ROBOUSE_WORKER_PYTHON" -m pip install .
```

Each family can use a different interpreter. Prepare its matching runtime and
assets independently; installing the worker bridge alone does not provide them.

Reviewed native versions:

- ManiSkill CPU/GPU: `mani-skill==3.0.1`, with the upstream CPU worker documenting
  `sapien==3.0.3`; its Torch, SAPIEN and planning/IK dependencies must be prepared.
- Genesis: `genesis-world==0.3.3`. The adapter checks this version and uses camera
  intrinsics/extrinsics from that release; actual native scene compatibility is
  still a runtime acceptance item.
- RoboCasa: the RoboUse 0.4.1 RoboCasa365 worker, its independent RoboSuite 1.5 /
  MuJoCo 3.3.1 / NumPy 2.2 stack, recorded layouts and Lightwheel kitchen assets.
  Use the separate `robocasa` runtime profile; do not reuse the LIBERO
  environment. The worker holds the base and torso fixed.
- Factory: Isaac Lab 2.3 / Isaac Sim 5.1, matching RoboUse's native implementation.
  Use `ROBOUSE_IL_RTX=0`; the current adapter rejects RTX mode rather than claiming
  unverified camera calibration. Its images are the **native MuJoCo proxy of the
  parts**, not an Isaac RTX camera. Calibration includes the proxy-origin offset,
  and each frame records `scene_is_proxy=true`.

Example (do not run until a suitable device is reserved):

```bash
export ROBOUSE_WORKER_PYTHON=/persistent/env/maniskill/bin/python
bash scripts/robouse/run_maniskill.sh \
  --task-dir "$TASK_ROOT/maniskill/maniskill-pick-cube" \
  --output-dir "$PWD/eval_outputs/robouse-maniskill-smoke/environment" \
  --run-id robouse-maniskill-smoke --no-depth --check-only

# GPU examples use the official exported task folder, not a generated instruction:
export ROBOUSE_WORKER_PYTHON=/persistent/env/genesis/bin/python
bash scripts/robouse/run_genesis.sh \
  --task-dir /path/to/exported/gpu/task \
  --output-dir "$PWD/eval_outputs/robouse-genesis-smoke/environment" \
  --run-id robouse-genesis-smoke --depth --check-only
```

`--check-only` does not require a simulator interpreter and does not validate its
installed dependencies. Before a real run, set the matching worker interpreter,
prepare assets, select an available GPU if needed, and remove `--check-only`.
All backend launchers use the shared agent script and tool collection.

Controller details deliberately kept within the integration:

- ManiSkill CPU uses its native accumulated target and IK. GPU delta control
  converts world poses to the robot base frame. Version 3.0.1's actual controller
  uses intrinsic XYZ Euler increments multiplied by negative `rot_lower`; the
  adapter follows source behavior, not the upstream summary's axis-angle wording.
- Genesis converts at the native fingertip TCP (including the native offset) and
  rejects partial aperture, because its grasp model is binary.
- Factory accepts only the initial finger aperture and upright orientation, rejects
  counterclockwise nut turns, and compensates the input EMA where saturation allows.
  It does not bypass native workspace clamps, decimation, or the task controller.
  Input compensation is not a claim that commanded poses are reached exactly.
- Model observations exclude object truth and controller internals. Worker-side
  internal command targets are used only to translate the requested action.
- No-depth capture retains calibration. ManiSkill depth is converted from native
  millimetres to metres; other supported renderers return metric depth. Captures
  do not step physics between cameras.

Relevant source boundaries: `environment/native_worker.py`,
`environment/worker_cameras.py`, `environment/worker_frames.py`,
`environment/backends/worker_backend.py`, and `worker_control.py`.
Text/refusal submission families are not supported.
