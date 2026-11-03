# Getting started

Install uni-robot-agent, check its configuration offline, then prepare a local
environment or connect to an existing episode. Simulator, hardware and model
runtimes run as separate services.
The CLI is `uni-robot-agent`, the Python package is `uni_robot_agent`, and
configuration environment variables use `UNI_ROBOT_AGENT_`.

The same installation provides the [Python SDK](sdk.md) and the local Web monitor.
Choose a workflow based on where the environment runs:

| Your starting point | Workflow |
|---|---|
| An environment service is already running | Install the agent, configure the model, then `run --env-url URL` |
| You want local RoboDojo or RoboUse | Install the agent, `setup` once, then `run --deploy local` for each episode |
| You want RoboLab | Prepare its native runtime, start the service, then connect with `--env-url` |
| You want a Python application or strategy | Install into your Python environment and use the [SDK](sdk.md) |

The package contains the CLI, SDK, prompts, skills and monitor. Simulator
runtimes, assets and optional model checkpoints are prepared separately.

## Install

Use Python 3.11+ in a virtual environment. Choose Python 3.11 for the simplest
local RoboDojo setup; its native runtime requires that version even when the
planner uses a newer interpreter. For example:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
```

The standard installation includes
the pinned Codex and Claude Agent SDKs, the Textual terminal interface, and the
shared HTTP service framework.

Install the CLI and SDK:

```bash
python -m pip install uni-robot-agent
uni-robot-agent --version
uni-robot-agent --help
```

For an isolated CLI, use `uv tool install uni-robot-agent`. For a uv Python
project, use `uv add uni-robot-agent`; a tool installation does not make the SDK
importable in your project's interpreter.

### Platform support

The release CI matrix targets Linux with Python 3.11/3.12 and macOS with
Python 3.12; inspect the workflow results for validation of a specific commit.
macOS can use the CLI/SDK with remote environment services and view saved runs;
local GPU simulators require their supported Linux host, drivers and native
runtimes. Native Windows is not supported by the POSIX deployment lifecycle;
use a supported Linux environment such as WSL2, subject to simulator requirements.
Installing the package alone does not verify model access or native runtime health.

### Install from source or a wheel

For development, install from a source checkout (repository access is required
while the GitHub repository remains private):

```bash
git clone \
  https://github.com/Open-RUA/uni-robot-agent.git uni-robot-agent
cd uni-robot-agent
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
uni-robot-agent --version
uni-robot-agent --help
```

For an isolated command-line installation, run `uv tool install .` from the
repository root instead. If uv reports that its executable directory is missing
from `PATH`, run `uv tool update-shell` and open a new terminal. Use pip when
importing `uni_robot_agent` in your own Python environment, or
`python -m pip install -e '.[dev]'` when modifying the source.

To install a release candidate without a checkout:

```bash
python -m pip install /path/to/uni_robot_agent-0.1.0-py3-none-any.whl
```

### Integration extras

Choose extras for the integrations you use:

| Extra | Installs |
|---|---|
| `robodojo` | RoboDojo integration dependencies; excludes Isaac and XPolicyLab |
| `robolab` | Geometry and collision dependencies; excludes Isaac and native RoboLab assets |
| `dev` | Build tools; combine with the integrations being developed |

Use `python -m pip install 'uni-robot-agent[robodojo]'` or
`uv tool install 'uni-robot-agent[robodojo]'`. From a source checkout, use
`python -m pip install '.[robodojo]'`.

Install integration extras in the environments that import their native adapters.
RoboDojo Hybrid bindings also need the `robodojo` extra in the
agent environment, even when all native services run elsewhere.
The base package does not install simulators, action-model checkpoints, or GPU
inference runtimes.

## Check the configuration

This command runs without a model credential or a running simulator:

```bash
uni-robot-agent run --integration robodojo --model YOUR_MODEL --env-url http://127.0.0.1:18760 \
  --action-profile direct --no-sam3 --validate-config
```

It should print `Configuration valid`. Replace `--validate-config` with
`--print-config` to inspect the resolved configuration. Both modes return before
connecting, resetting an environment, creating output directories or using a
GPU. Both `run` and `tui` require an explicit `--integration`. This example selects
`robodojo`, which connects to an official RoboDojo episode.
Offline validation checks CLI settings; it does not verify
endpoint availability, checkpoint compatibility, credentials or model support.

## Prepare and run locally

On a supported Linux GPU host, a published installation prepares its matching
version without requiring a source checkout:

```bash
uni-robot-agent setup --integration robodojo
```

For an unreleased checkout, build a wheel at the repository root and pass it to
setup. This also applies during the source preview:

```bash
python -m pip wheel --no-deps . -w dist
uni-robot-agent setup --integration robodojo \
  --wheel dist/uni_robot_agent-0.1.0-py3-none-any.whl
```

If you installed a downloaded wheel, pass its actual path to `--wheel`; you do
not need to clone the repository or rebuild it.

RoboDojo's runtime recipe requires Python 3.11 (`python3.11` on PATH, or pass
`--python /path/to/python3.11`), even if the outer CLI uses Python 3.12.

Setup prepares the runtime, source and assets without starting a service. To reuse
an existing installation, follow [host prerequisites and adoption](deployment.md).

Choose a model and a Responses-compatible provider:

```bash
export UNI_ROBOT_AGENT_API_KEY='replace-with-provider-api-key'
export MODEL='YOUR_MODEL'

uni-robot-agent run --integration robodojo --deploy local \
  --task stack_bowls --model "$MODEL" \
  --provider-url https://provider.example/v1 \
  --action-profile direct --no-sam3 \
  --output-dir ./runs --monitor --monitor-port 8765
```

The command starts the prepared `stack_bowls` environment, runs one task and
stops the processes it owns. Install dependencies beforehand. Deploy actor and
SAM3 services separately when needed; this Direct example uses neither.
RoboDojo includes depth by default. To use the paper's **Realistic** RGB-only
input setting, add `--no-depth` to this local deployment command and retain
camera calibration; see [observation settings](integrations.md#realistic-observation-settings).
For RoboUse, select its [runtime profile](deployment.md#environment-profiles-and-limits)
and task. Use the connection workflow below if services are already running.

## Prepare the services

Keep the agent, simulator and inference dependencies in separate environments.
For `--env-url` mode, start the services you need before running an episode.
`deploy env`, `deploy actor` and `deploy sam3` run prepared services independently;
see [foreground services](deployment.md#foreground-services).

| Integration | Runtime requirements |
|---|---|
| RoboDojo environment | Pinned official RoboDojo checkout, Isaac runtime, assets and frozen layout; see [provenance](../uni_robot_agent/integrations/robodojo/SOURCE.md) |
| RoboUse environment | A prepared native runtime for the selected profile and task; see [deployment](deployment.md) |
| RoboDojo Pi0.5 actor, when enabled | Separate OpenPI/XPolicyLab runtime and matching checkpoint/profile |
| SAM3, when enabled | Separate environment with an appropriate PyTorch build, `sam3==0.1.4`, `pydantic>=2,<3`, the base harness and a checkpoint |

The base package includes HTTPX, FastAPI, Pydantic and Uvicorn. RoboDojo Pi0.5
uses the shared HTTP actor client, so the agent environment does not
need OpenPI/JAX or `openpi-client`. Those dependencies belong to
the configured actor runtime. SAM3 also keeps its GPU dependencies in its own
service environment; there is no extra that installs its native runtime.

Configure the Pi0.5 runtime, checkpoint and observation/action profile with
one of the [service examples](../examples/services/) and use the
[unified service entrypoints](services.md#start-services).

Use the [service entrypoints](running.md#scripts) with the appropriate
`SIM_PYTHON`, `ACTION_MODEL_PYTHON` or `PYTHON` for your machine. Run on trusted
machines and networks: agents can access their workspace, and the service RPC
endpoints do not provide a public internet authentication boundary.

## Run a RoboDojo episode

This workflow connects to a running RoboDojo episode. The base agent installation
is sufficient for the Direct example below; simulator dependencies belong in the
environment service. For Hybrid control, also install the `robodojo` extra in the
planner environment using the [integration extras](#integration-extras) instructions.

The service must already be running with the task and run ID below. Set your
model ID, provider URL and API key.
The Codex default `passthrough.responses` profile expects a Responses-compatible upstream.
`passthrough.responses` preserves the SDK's declared tool names and namespaces. Profiles for
providers that require flat tool lists encode namespaces into distinct names
and restore the SDK routing fields in responses.

```bash
export UNI_ROBOT_AGENT_API_KEY='replace-with-provider-api-key'

uni-robot-agent run --integration robodojo --agent codex --model YOUR_MODEL \
  --provider-url https://provider.example/v1 \
  --env-url http://127.0.0.1:18760 \
  --task stack_blocks --run-id YOUR_SERVER_RUN_ID \
  --action-profile direct --no-sam3 \
  --output-dir outputs/robodojo
```

Replace the model, provider, task and run ID with your deployment's values.
`--task` and `--run-id` are optional identity checks. If supplied, they must match
the server; they do not reconfigure it. Add `--validate-config` to check the CLI
settings before executing it. Without an offline flag, this starts an episode.
The environment supplies the task instruction unless you set `--instruction`.

Direct control needs no action model or SAM3 in this example. For hybrid control,
provide an HTTP `--action-model-url` and `--action-model-checkpoint-sha256`.
The actor advertises its model identity and profile before creating an explicit
HTTP session. The VLA reviewer is enabled by default for RoboDojo hybrid actions
and needs an API endpoint and credential at execution time. These can be configured
independently or inherited from the main agent; see
[reviewer configuration](running.md#vla-reviewer). The
[minimal Hybrid example](../examples/robodojo_hybrid.sh) connects to separately
prepared environment and actor services, with normal budgets and task supplements.
Supply your model ID and the actor checkpoint hash; see
[service deployment](deployment.md#foreground-services).

## Run RoboUse tasks

RoboUse includes LIBERO and RoboCasa through separate native profiles. Select
`--integration robouse`; the standalone LIBERO integration is not included.
Prepare the profile with `setup --integration robouse --runtime-profile libero`.
For an unreleased build, also pass your wheel with `--wheel`; a published version
is installed automatically into the runtime. Select a task from the prepared
catalog for `--deploy local`. Use Direct mode and `--no-sam3` for the
minimal configuration. See [profiles and task selection](deployment.md#environment-profiles-and-limits)
and the [RoboUse guide](../scripts/robouse/README.md) for native requirements.

## Use the Claude agent

The pinned Claude Agent SDK is included in the standard installation.

Claude Code uses the uni-robot-agent gateway with a Messages-compatible upstream. Set
`UNI_ROBOT_AGENT_API_KEY`, then connect to an existing environment:

```bash
uni-robot-agent run --integration robodojo --agent claude_code \
  --model YOUR_CLAUDE_MODEL --provider-url https://provider.example/v1 \
  --provider-profile passthrough.messages \
  --env-url http://127.0.0.1:18760 \
  --action-profile direct --no-sam3 --output-dir ./runs
```

The same provider flags work with `--deploy local`. Use `--claude-effort` and
`--claude-max-budget-usd` for Claude-specific controls; omit Codex reasoning and
MCP dispatch options. Provider timeouts/retries also apply to Claude. See
[agent configuration](running.md#single-episode-cli) and
[reviewer configuration](running.md#vla-reviewer) for optional Hybrid execution.

## View one task in the terminal

The terminal interface is included in the standard installation. It connects to
an already running service; it does not accept `--deploy local` or `--monitor`.
For a local environment, keep this command running in one terminal:

```bash
uni-robot-agent deploy env --integration robodojo --task stack_bowls
```

In another terminal, with your provider credential set:

```bash
uni-robot-agent tui --integration robodojo --model YOUR_MODEL \
  --provider-url https://provider.example/v1 \
  --env-url http://127.0.0.1:18760 --action-profile direct --no-sam3
```

The TUI connects to the same services and accepts one task. It keeps the event
view open after completion; use `/status` or `/quit`. Start another TUI for
a new task; conversation continuation and `/reset` are not supported. Omit
`--instruction` and `--episode-timeout-s`, which belong to `run`. Use `run`
when you need a finalized single-episode result.

Skills are discoverable and readable by default. Call `list_skills()` without
arguments to get the shared and environment namespaces in `paths` and their
sorted recursive Markdown paths in `entries`. Pass a relevant file path to `read_skill(path=...)`
to obtain its `content` and `sha256`. Missing namespaces contribute no entries and are not created.
To allow writes, configure `UNI_ROBOT_AGENT_SKILLS_DIR` as a complete external
skills library, then add `--skill-writing`. All skill tools use that library;
there is no fallback to packaged files and no special `memory/` directory.
See [skills setup](running.md#skills) for a small initialization example. Review
updated skills before reusing them in later tasks.

## Watch and replay in the browser

Add `--monitor` to a `run` command for a live console. Open the printed URL;
forward its port when running on a cluster. The console shows a primary camera
with other cameras in small views, tool batches, execution queues and timing,
step usage and native outcomes. A view holds its previous frame until a new
recorded observation arrives.

After the run, open existing result directories:

```bash
uni-robot-agent monitor --run-dir ./runs --port 8765
# Or select particular runs:
uni-robot-agent monitor --run-dir ./runs/run_a ./runs/run_b --port 8765
```

Multiple runs open a card overview. This is local directory replay, without
upload or model/environment access. Keep environment snapshot archives for
intermediate physical-step images; planner tool images alone may be sparse.
See [Web monitor](running.md#local-web-monitor) for archive layout, live versus
completed behavior, playback controls and historical-data limits.

## Inspect the result

Each `run` creates a unique directory beneath `--output-dir`:

| Artifact | Contents |
|---|---|
| `<run>/result.json` | Resolved configuration, native task result, acceptance/retry classification and export paths |
| `<run>/tools.json` | The tool schemas exposed to the agent for this run |
| `<run>/native_outcome.json` | Environment-reported outcome, when episode finalization completes |
| `<run>/finish_request.json` | The reason, status and summary submitted to finish the episode |
| `<run>/images/` | Shared image files, addressed by content hash |
| `<run>/artifacts/` | Non-image artifacts and artifact metadata |
| `<run>/trajectory.jsonl` | Agent and tool events, including image references; interactive turns append to this file |
| `<run>/codex_responses/` | Recorded provider exchanges for Codex with an explicit provider URL |
| `<run>/train.jsonl` | Codex training sample when export validation succeeds |

Hybrid runs also record action proposals, reviewer decisions and execution
receipts in `<run>/actor_sessions/`. Read the most recently written result from
the RoboDojo example:

```bash
python - <<'PY'
import json
from pathlib import Path

results = list(Path("outputs/robodojo").glob("*/result.json"))
if not results:
    raise SystemExit("No result.json found; inspect the run log.")
path = max(results, key=lambda item: item.stat().st_mtime_ns)
result = json.loads(path.read_text())
print(path)
for key in ("task_success", "agent_finished", "accepted", "retryable", "failure_category"):
    print(f"{key}: {result[key]}")
PY
```

`task_success` records the environment's outcome; `agent_finished` records
agent completion. `accepted` indicates whether the run is accepted for evaluation;
`retryable` and `failure_category` describe its retry classification and failure
category. An accepted run may be an ordinary task failure. These fields do not
schedule retries. The CLI and SDK run one episode per invocation/session.

The two finish files are written as soon as a completed environment receipt is
received, including a receipt recovered after a timeout. Each JSON file is
published atomically; the pair is not a multi-file transaction. A failed finish
file write is logged and reported by filename in the optional `storage_errors`
object in `result.json`, without changing the confirmed task outcome or replaying
finish. Normal results omit this field.

Image references in observations, trajectories, provider traces and training
samples are relative to the run directory. Resolve them against that directory
when reading a copied run. Shared images remain available with
`--no-save-exchanges`; the switch never deletes them. Environment-owned videos
and other remote files retain their existing locations and lifecycle.

The session drains its local artifact writes before publishing `result.json`.
Local storage failures are reported independently of the native task outcome;
this does not certify that a remote environment archive was delivered. Failure
to write the main result is logged and prevents a zero process exit status;
it does not enable automatic retries. Forced termination can still prevent a
final result file.

`result.json` identifies partial training samples and export errors. A
`train.invalid.jsonl` file is a diagnostic record, not a validated sample.
Codex native-provider mode can run without `--provider-url`, but cannot produce
the trusted SFT export. `--telemetry debug` additionally saves raw requests.

Continue with [configuration and running](running.md),
[architecture](architecture.md), or [environment integrations](integrations.md).

## Use Python

The installed CLI package is also importable. Use `Session(SessionConfig(...))`
for a custom strategy, or `run(SessionConfig(agent=AgentConfig(...), ...))` for a
built-in planner. Both use the existing prepared episode and tool contracts.
See [Python SDK](sdk.md) for complete examples, explicit `setup()` / `deploy()`,
resource ownership, events and lifecycle semantics. SDK results can be viewed
with the same `monitor --run-dir` command.
