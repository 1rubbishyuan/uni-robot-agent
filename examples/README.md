# Run an example episode

These scripts run one task with Direct tools and no action-model or SAM3 service.
They use the normal CLI, its default budgets, and your provider configuration.
All paths and endpoints below are local examples; substitute your own values.
The shell scripts live in the source repository and are not installed as CLI
commands by pip. For a workflow without a checkout, use the
[CLI quick start](../README.md#quick-start) or the [Python SDK](../docs/sdk.md).

| Script | Example task | Environment lifecycle |
|---|---|---|
| [robodojo.sh](robodojo.sh) | `stack_bowls` | `setup` once, then `run` starts and cleans up an environment |
| [robolab.sh](robolab.sh) | `BananaInBowlTask` | Prepare native RoboLab, then `serve` and `run` in two terminals |
| [robouse.sh](robouse.sh) | LIBERO `libero-10-0` | `setup` once, then `run` starts and cleans up an environment |

## Install and configure the planner

Run these commands from the source repository root, in your planner environment
with Python 3.11 or newer:

```bash
python -m pip install -e .
python -m pip wheel --no-deps . -w dist
export WHEEL="$PWD/dist/uni_robot_agent-0.1.0-py3-none-any.whl"
```

Pass this wheel to `setup` so the native runtime receives the same source build.
This is required during the source preview and whenever you use unreleased code.
A published installation can omit `--wheel` when using the CLI directly. The scripts use `python` from PATH;
set `PYTHON=/path/to/planner/python` to select another planner interpreter.

For the default Codex agent, use a vision-capable model with tool calling through
a Responses-compatible provider. Set these in the shell running the planner:

```bash
export MODEL='YOUR_MODEL_ID'
export UNI_ROBOT_AGENT_PROVIDER_URL='https://provider.example/v1'
export UNI_ROBOT_AGENT_API_KEY='YOUR_API_KEY'
```

Replace the placeholders with your provider's values. Keep credentials in the
environment, not in the scripts. Agent/provider flags are forwarded to the CLI;
see [other provider profiles and Claude](../docs/getting-started.md#use-the-claude-agent).

## RoboDojo

Use a supported Linux NVIDIA GPU host. Setup requires Python 3.11, downloads the
pinned simulator/resources, and prepares an isolated runtime. See
[host prerequisites](../docs/deployment.md) before setup.

```bash
# Once; use --python /path/to/python3.11 if it is not on PATH.
bash examples/robodojo.sh setup --wheel "$WHEEL"

# Offline check, then execute one task.
bash examples/robodojo.sh run --model "$MODEL" --validate-config
bash examples/robodojo.sh run --model "$MODEL" --monitor
```

The script selects `stack_bowls` and keeps the default task supplements enabled.
Pass `--task OTHER_TASK` to change tasks, `--env-gpu 1` to choose another GPU,
or `--no-depth` for RGB-only input with camera calibration. Only seed 0 is
supported by the bundled RoboDojo runtime.

## RoboUse (LIBERO)

Use a supported Linux GPU/EGL host with Python 3.11 available. LIBERO has its own
runtime profile because its dependencies differ from the other RoboUse backends.
Setup prepares the native packages and assets; see
[runtime profiles](../docs/deployment.md#environment-profiles-and-limits).

```bash
bash examples/robouse.sh setup --wheel "$WHEEL"
bash examples/robouse.sh run --model "$MODEL" --validate-config
bash examples/robouse.sh run --model "$MODEL" --monitor
```

The default bundled task, `libero-10-0`, asks the robot to put the alphabet soup
and tomato sauce in the basket. Select another admitted task with `--task`.
For another backend, pass the same `--runtime-profile` to both `setup` and `run`
and choose a task from that profile, for example a prepared RoboCasa task.
An offline argument check does not establish that a selected task or runtime is
installed. See [RoboUse task coverage](../scripts/robouse/README.md).

For both managed examples, omit `UNI_ROBOT_AGENT_ENV_URL` from the invoking
environment: an external URL conflicts with `--deploy local`. To connect to an
already running service instead, use the [CLI connection workflow](../docs/deployment.md#run-a-task-with-one-command).

## RoboLab

First follow [RoboLab installation and asset preparation](../docs/robolab.md#installation-and-native-preparation).
RoboLab has no automatic `setup`/`deploy` recipe. Its supported Isaac runtime,
benchmark tasks and assets must already be installed; installing the planner
alone does not prepare them. Install this checkout with the `robolab` extra in
that native environment as described in the guide.

In terminal 1, start a single environment and wait for service startup to finish:

```bash
export SIM_PYTHON=/absolute/path/to/prepared/robolab/python
bash examples/robolab.sh serve --output-dir "$PWD/runs/robolab/environment-001"
```

In terminal 2, activate the planner environment and set its model/provider
variables as above:

```bash
bash examples/robolab.sh run --model "$MODEL" --validate-config
bash examples/robolab.sh run --model "$MODEL" --monitor
```

Both commands default to `BananaInBowlTask`, seed 0 and instruction type `default`.
If overriding these, use matching values for `serve` and `run`. For another port,
pair `serve --port 18771` with `run --env-url http://127.0.0.1:18771`.
The simulator uses `--device cuda:0` by default. Add `--record-video` to `serve`
if `ffmpeg` is available in the native environment.

The agent finalizes the episode; the foreground service remains owned by terminal
1. Stop it with Ctrl-C, then restart with a new output directory before another
episode. Never reuse `environment-001` for a second launch.

## Options and results

Each script's `--help` explains its modes. Arguments after `setup`, `serve` or
`run` are forwarded unchanged; later CLI arguments override the example defaults.
Use `run --model "$MODEL" --print-config` to inspect the resolved settings.
Offline checks do not call the model or start a simulator, and do not verify
credentials, GPU availability or task success.

Agent outputs go under `./runs/robodojo`, `./runs/robolab` or `./runs/robouse`.
Each run gets its own session directory. `--monitor` prints the live Web URL;
use `--monitor-port` if running examples simultaneously. To replay saved results:

```bash
python -m uni_robot_agent monitor --run-dir ./runs/robodojo
```

For Python integration, see [SDK examples](sdk/). The separate
[Hybrid example](robodojo_hybrid.sh) and [service configurations](services/)
cover optional action-model and segmentation services.
