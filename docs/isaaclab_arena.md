# IsaacLab Arena humanoid integration

`isaaclab_arena` connects humanoid simulation to the existing `actor_act` tool.
The planner observes, chooses bounded actor phases and checks their effects. The
binding adapts G1 semantics, while a separate GR00T service performs inference
and Arena's native whole-body controller executes coordinated motion.

## Admitted environment and model

The pinned runtime currently admits `galileo_g1_locomanip_pick_and_place` with
`g1_wbc_joint`: move the brown box from the shelf into the blue bin. The matching
actor recipe is `arena_g1_gr00t`. Other Arena tasks and checkpoints require their
own validated profiles; this integration does not claim universal task success.

The robot observes head RGB and 31 joint positions. GR00T predicts 50 samples of
joint, walking-velocity and height references. The native controller owns balance,
legs and waist. The model's three waist compatibility outputs remain in the trace
but do not become independent controls. Velocities use the base-heading frame in
m/s and rad/s; pelvis height is a ground-relative controller reference in metres.

Only shared scene, hybrid, episode and skill tools are composed. Gripper/Direct
motion tools are unavailable. Reviewer modes are student and stop, with empty
constraints. Review is optional and does not define native task success.

## Separate runtimes

The planner, actor and simulator have independent interpreters:

- GR00T: Python 3.12, pinned official N1.6 source, Torch 2.7.1/cu128. The recipe
  verifies the official dependency lock SHA256 and installs it with `--frozen`.
  The four TensorRT/CUDA13 export packages are excluded; inference uses the
  official PyTorch `Gr00tPolicy` and processor directly. It does not require another inference server.
- Arena: Python 3.11, Isaac Sim 5.1 and the official
  `feature/arena_v0.2_on_lab_2.3` branch at the revision in `SOURCE.md`.
  Physics runs on CPU PhysX, as in this branch's published evaluation; RTX
  rendering still requires a supported NVIDIA GPU. Setup never changes drivers.
  Headless graphics, native libraries and Kit initialization must pass native
  acceptance on the target host.

The actor requires a working C compiler (`CC`) and CUDA 12.8 toolkit
(`CUDA_HOME`) for its native dependency initialization. Setup verifies them
without modifying system compilers, drivers or libraries. Supply both as
`--runtime-env` values so the prepared runtime retains them.

Do not install both native dependency stacks into the planner environment. Install
matching harness/runtime versions into their respective interpreters. The public
weights use NVIDIA's noncommercial license; review the linked model card.

```bash
uni-robot-agent setup --service actor --profile arena_g1_gr00t \
  --runtime-env CC=/path/to/gcc --runtime-env CUDA_HOME=/path/to/cuda-12.8
```

Actor setup downloads pinned source and inference weights into its runtime cache.
The actor downloads 6.57 GB of model and processor files, excluding optimizer
and training-state shards. Arena and GR00T do not share a Python environment. On an already
prepared official native environment, adopt it explicitly:

```bash
uni-robot-agent setup --integration isaaclab_arena \
  --runtime-python /path/to/arena/python --native-root /path/to/arena
uni-robot-agent setup --service actor --profile arena_g1_gr00t \
  --runtime-python /path/to/gr00t/python --native-root /path/to/gr00t \
  --checkpoint /path/to/checkpoint
```

Arena currently adopts a prepared native runtime, following the pinned branch's
[official installation](https://github.com/isaac-sim/IsaacLab-Arena/blob/aad4f25f44cc439c363c2b60f1fd170dfd10d2a5/docker/Dockerfile.isaaclab_arena).
Install the matching harness runtime in that interpreter first. The upstream
Isaac Sim and Lab pip metadata contain conflicting web dependencies; the harness
does not claim a validated automatic installer for this native stack.

Setup records and verifies the actor checkpoint digest. Supply that same digest
as `--action-model-checkpoint-sha256` to the planner. Deployment never repairs or
upgrades a prepared runtime. See [deployment](deployment.md) for cache and ownership.

## Run a planner episode

Start the actor and environment in separate terminals, then run the planner. The
[complete example](../examples/isaaclab_arena_hybrid.sh) uses `actor_act` with the
reviewer disabled. Configure the planner provider and model with the normal CLI
options; there is no actor-only agent mode.

The simulator advances in 20 ms control steps, each containing four physics
substeps. Agent and inference latency pause simulation. This synchronous evaluation
does not demonstrate real-time humanoid operation. Cancellation stops after the
current atomic sample; unknown acknowledgements are reconciled, never replayed.

## Validation and current limitations

CPU checks cover exact profile mapping, native action packing, invalid-command
rejection, cancellation/terminal handling and model-session failure behavior.
A compatible GPU host must additionally validate startup, camera images, WBC,
seeded reset, terminal-frame preservation and native-policy versus harness replay.
Then run the actual planner with fresh observations and bounded actor calls.

The server creates Kit and runs native operations on the main thread; HTTP
workers submit operations to that owner. Native startup and service execution
must both pass acceptance before claiming an operational environment. No GPU
rollout score is implied by a successful dependency preflight.

Native success uses this release's `objects_in_proximity` box/bin predicate
(x/y/z thresholds 0.260/0.130/0.150 m). Its thirty-second timeout and drop
termination remain unchanged. This is the pinned Isaac 5.1 benchmark definition,
not the redesigned task predicates on Arena main. Calling finish,
reviewer approval, target tracking and model predictions do not set success.
Source and weight identities are listed in [SOURCE.md](../uni_robot_agent/integrations/isaaclab_arena/SOURCE.md).

The initial head image is refreshed through the native renderer before it reaches
the planner. This checks both physics counters and the complete native reset-state
fingerprint without consuming control steps.

Before PhysX creates its first contacts, the box spawn is aligned with the pinned
upstream reset sampler for the requested seed. The ordinary native reset still
runs, and its actual PhysX pose must match that sample exactly. This prevents
high-friction contact anchors from the midpoint spawn acting across the first
randomized reset. Asset geometry, materials, solver parameters, reset ranges and
termination rules are retained; Python random state is restored after sampling.

Native hold checks for seeds 0, 1 and 2 each completed 150 control steps with no
termination. All three reset poses matched the upstream sampler component by
component, and maximum box displacement stayed below 44 micrometres. These checks
validate startup stability, not planner task success. Earlier rollouts containing
the startup box ejection remain diagnostic and must not be counted as task scores.
