# DexJoCo Hybrid

DexJoCo connects the shared Hybrid tools to the native MuJoCo Panda/Allegro
controller. The initial supported task is `pick_bucket`, with the publicly
released task-specific Pi0.5 weights. Direct motion and EEF corrections are not
available; actor execution supports optional student/stop review.

## Sources and runtimes

- Environment and OpenPI source: [brave-eai/dexjoco](https://github.com/brave-eai/dexjoco),
  revision `8d23b0fab23b17a58c4b55f3942e17013aaf8267`.
- Model: [DexJoCo/DexJoCo-Pi05](https://huggingface.co/DexJoCo/DexJoCo-Pi05),
  revision `8d253e04e1b82c452c5804273939ff7f002f217f`,
  `pi05_dexjoco_ckpt/pick_bucket`.
- Simulator: Python 3.11, MuJoCo 3.4.0, Gymnasium 1.0.0 and NumPy 1.26.4.
- Actor: a separate pinned OpenPI/JAX environment. Its model recipe controls
  dependencies, input layout, training config, normalizer and source checksums.

On Linux, native dependency installation also requires a C compiler and Python
development headers for dependencies without a matching prebuilt wheel.

The simulator and model have different NumPy requirements. Keep separate Python
environments and connect them through the existing HTTP services. The actor's
upstream client has a conflicting NumPy metadata pin; the recipe records the
specific compatibility exception rather than ignoring arbitrary dependency errors.

## Prepare and launch

Install a version containing this integration. For an unreleased checkout,
[build both distributions](deployment.md#prepare-an-unreleased-build), install
the planner wheel and pass its matching runtime wheel to **each** setup command.

```bash
uni-robot-agent setup --integration dexjoco
uni-robot-agent setup --service actor --profile dexjoco_pick_bucket
```

Setup obtains the pinned sources and only the selected checkpoint's inference
parameters and normalization assets. `--checkpoint /absolute/path/to/pick_bucket`
reuses downloaded weights. It does not need the training optimizer state.
Existing prepared runtimes can be adopted using `--runtime-python` and
`--native-root`; actor adoption additionally requires `--checkpoint`.

Launch the services in separate terminals. These commands select already
prepared runtimes; they do not install packages:

```bash
uni-robot-agent deploy actor --profile dexjoco_pick_bucket --gpu 0 --port 18761
uni-robot-agent deploy env --integration dexjoco --task pick_bucket \
  --seed 0 --no-depth --gpu 1 --port 18788
```

Both default to loopback. On separate hosts, explicitly configure the bind
address and use addresses reachable by the planner. For direct service startup,
see [`openpi_dexjoco_single.toml`](../examples/services/openpi_dexjoco_single.toml).
The pinned training-config loader reads `config.yaml` from `native_root/openpi`;
managed deployment sets this working directory. Direct actor launches must do so
before importing OpenPI. The recipe verifies the YAML as well as Python sources.

Obtain the verified checkpoint hash from the actor's `/info` response, then run:

```bash
ENV_URL=http://127.0.0.1:18788 \
ACTOR_URL=http://127.0.0.1:18761 \
ACTOR_CHECKPOINT_SHA256=YOUR_VERIFIED_SHA256 \
  bash examples/dexjoco_hybrid.sh --model MODEL \
  --provider-url https://your-provider.example/v1 --reasoning-effort medium
```

Provide credentials through the selected provider's environment configuration.
The example enables runtime skills, requires no SAM3 service, and sets a
three-hour episode timeout with no planner turn limit. Reviewer configuration
uses the existing Hybrid options; `--no-vla-review` disables the reviewer while
keeping planner-directed actor calls and the same native step budget.

## Physical contracts

| Boundary | Single-arm representation |
|---|---|
| Observations | Measured world TCP xyz, quaternion wxyz, sixteen named finger positions: 23 scalars |
| Images | Front and wrist RGB, with native camera calibration; no simulator depth |
| Actor output | Thirty rows of absolute world TCP xyz, rotation-vector radians and sixteen finger-position radians: 22 scalars per row |
| Model sampling | Thirty rows in the loaded model's 32-wide latent space |
| Native command | Quaternion TCP target plus all finger targets, applied in one control step |

The binding validates frame, control point, units and field order. It converts
rotation vectors to quaternions and records finger-target clipping to the
published native actuator ranges, including both raw and applied predictions.
The adapter uses the original operational-space torque controller and finger
position actuators together. It does not replace the arm controller with IK.

Native control time is 0.02 seconds per action, with a 1200-step task budget.
The upstream client's wall-clock rendering/scheduling rate is a separate setting.
A complete coordinated command is validated before physics advances; one
acknowledgement establishes one sample. A stale proposal or invalid finger
target rejects the whole command. Lost acknowledgements require receipt
reconciliation through the shared service/client, never automatic replay.

The adapter removes privileged object/table state before publishing any snapshot.
Task-verifier success is retained for final evaluation results and is not sent
as model-visible action feedback. Native termination and the task budget stop
further actions. Constructor seeding and an opaque initial-state fingerprint
support comparison; native `reset(seed=...)` alone does not reset all upstream RNGs.

The binding's dual-arm layout has contract fixtures, but the public native task
admission and model recipe are single-arm. These fixtures do not establish
support or scores for all DexJoCo tasks.

## Validation and baselines

Native acceptance runs the official environment and checks repeatable reset,
measured observations, coordinated actions, camera calibration, original-control
parity and HTTP receipt behavior:

```bash
python scripts/validation/dexjoco_native_acceptance.py \
  --native-root /absolute/path/to/dexjoco --seed 0 --output-dir /new/native-check
```

Use the simulator interpreter with EGL configured for this check. To evaluate an
actor-only baseline, start fresh environment/actor services and use the planner
interpreter:

```bash
python scripts/validation/dexjoco_actor_baseline.py \
  --env-url http://127.0.0.1:18788 --action-model-url http://127.0.0.1:18761 \
  --checkpoint-sha256 YOUR_VERIFIED_SHA256 --seed 0 \
  --reset-sha256 YOUR_FRESH_RESET_SHA256 --output-dir /new/actor-baseline
```

The client requires a fresh prepared episode and never resets an existing one.
By default, its chunk count covers the native step budget; a shorter explicit
`--max-chunks` is labeled as a bounded smoke run in the result.

This baseline uses the same binding, actor runner and native executor as Hybrid,
without a planner or reviewer. Compare against Hybrid with the same checkpoint,
seed, initial fingerprint and native budget. The official DexJoCo evaluator uses
asynchronous prediction and overlapping action blending; this synchronous harness
baseline pauses physics during inference and discards unused chunk suffixes.
Report those baselines separately. Successful integration checks are not a full
benchmark success rate or evidence of improvement from Hybrid.
