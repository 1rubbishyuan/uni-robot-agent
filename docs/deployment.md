# Local setup, deployment, and task execution

The CLI/planner package and native runtime use separate Python environments.
Never install `uni-robot-agent` and `uni-robot-agent-runtime` in the same environment.
Since 0.1.1, `setup --wheel` accepts only a matching runtime wheel and rejects a
main-package or mismatched-version wheel before starting installation. Build both
with `scripts/release/prepare.py`; see [Packaging](packaging.md).

Prepare resources once, then launch services without installing dependencies in
`deploy` or `run`. Each service runs in its own interpreter and process group.
The CLI itself does not import Isaac, OpenPI, PyTorch, or native simulators.

Install the CLI using the [installation guide](getting-started.md#install).
If you already have an environment service, skip setup and use
[`run --env-url`](#run-a-task-with-one-command).

## Prepare a published version

For a published package, setup installs `uni-robot-agent-runtime` at the exact CLI version from the
configured package index. Prepare only the runtimes you need:

```bash
uni-robot-agent setup --integration robodojo
uni-robot-agent setup --integration robouse --runtime-profile mujoco
uni-robot-agent setup --service actor --profile robodojo_pi05
uni-robot-agent setup --service sam3
```

## Prepare an unreleased build

From the repository root, build both distributions and pass the runtime wheel
to each required setup command. This keeps the native runtime on the same source build as the planner:

```bash
python -m pip install 'build>=1' 'twine>=6' 'tomli-w>=1'
python scripts/release/prepare.py --output-dir ../candidate-0.1.1
python -m pip install ../candidate-0.1.1/dist/uni_robot_agent-0.1.1-py3-none-any.whl
uni-robot-agent setup --integration robodojo \
  --wheel ../candidate-0.1.1/dist/uni_robot_agent_runtime-0.1.1-py3-none-any.whl
```

If you installed a downloaded wheel, pass its actual path directly; no checkout
or build step is needed. Rebuild the wheel after source edits before preparing
a new runtime. Changing inputs for an existing cache entry requires another
`--cache-dir`.

## Host prerequisites and runtime storage

RoboDojo setup applies a documented [IsaacLab build metadata correction](../uni_robot_agent/integrations/robodojo/SOURCE.md#isaaclab-build-compatibility-011)
to keep its Starlette requirement compatible with Isaac Sim 5.1. The original
checkout and native Python modules are unchanged.

Setup also runs the native robot configuration generator in a private asset
overlay. Downloaded geometry stays linked; generated `curobo.yml` files belong to
this runtime, so `--assets-dir` never rewrites another runtime's configuration.

RoboDojo requires a Python 3.11 interpreter for its native runtime. Provide
`python3.11` on PATH or select it with `--python /path/to/python3.11`. The CLI's
Python version and the simulator runtime's Python version are independent.

The wheel contains the agent code. Setup downloads simulator assets and model
weights separately, so allow enough time and disk space. A Direct run with
`--no-sam3` needs only an environment runtime.

`setup` creates an isolated environment, installs the runtime package and pinned native
resources, then writes `runtime.json` after import and resource checks pass.
`validation.json` records whether the runtime was adopted or installed; GPU
validation is separate. New installations also save `installed.txt`. Source and
asset revisions and recipe digests are recorded separately from Python dependencies.

Runtimes live below `~/.cache/uni-robot-agent`, or `--cache-dir` /
`UNI_ROBOT_AGENT_CACHE_DIR`, keyed by service, harness version and recipe digest.
Preparation is locked, completed steps are reused after an interruption, and the
final manifest is published atomically. Different preparation inputs require a
separate cache directory; they never silently replace an existing runtime.

Select the base interpreter with `--python /path/to/python` and a local runtime
build with `--wheel /path/to/uni_robot_agent_runtime-0.1.1-py3-none-any.whl`. Prepare Linux, NVIDIA drivers,
EGL/Vulkan, sufficient disk space and any required compiler or CUDA toolkit first.
Setup does not use sudo or modify the system interpreter. For gated SAM3 assets,
configure Hugging Face access in the process environment before setup.

On Linux, the MuJoCo profile's `robosuite` dependency pulls in `evdev`, which may
need a C compiler, Python development headers, and matching Linux input headers.
With a Conda compiler, align its header search path with the headers used by
`evdev`'s code generator. These are host build prerequisites, not simulator assets.
After resolving host dependencies, rerun setup with the same inputs to resume.

The actor recipe preserves the pinned upstream OpenPI lock's NumPy override.
Only its exact known `rerun-sdk==0.26.2` / `numpy==1.26.4` metadata conflict is
accepted and recorded in the runtime; other dependency conflicts fail setup.
Rerun visualization is not part of the actor inference path.

## Reuse an existing runtime

For an existing installation, register it without modifying it:

```bash
uni-robot-agent setup --integration robodojo \
  --runtime-python /path/to/venv/bin/python --native-root /path/to/RoboDojo
uni-robot-agent setup --integration robouse --runtime-profile robocasa \
  --runtime-python /path/to/server/bin/python --worker-python /path/to/worker/bin/python
uni-robot-agent setup --service actor --profile robodojo_pi05 \
  --runtime-python /path/to/openpi/bin/python --native-root /path/to/XPolicyLab \
  --checkpoint /path/to/checkpoint
uni-robot-agent setup --service sam3 \
  --runtime-python /path/to/sam/bin/python --checkpoint /path/to/sam3.pt
```

Every selected interpreter, including an external worker, must have the same
harness version installed. `--runtime-env KEY=VALUE` supplies explicit native
settings such as Vulkan ICD and library paths. Do not put secrets in these
settings; they are persisted. Deployment can select an external interpreter
directly with the same flags, but validates it without installing or registering.

## Foreground services

```bash
uni-robot-agent deploy env --integration robodojo --task stack_bowls --gpu 0
uni-robot-agent deploy actor --profile robodojo_pi05 --gpu 1
uni-robot-agent deploy sam3 --gpu 2
```

Defaults bind localhost, with ports 18760 (RoboDojo), 18780 (RoboUse),
18761 (actor) and 18762 (SAM3). Override `--host`, `--port`, `--gpu`,
`--startup-timeout-s`, and `--shutdown-timeout-s` as needed. GPU indices refer to
the current `CUDA_VISIBLE_DEVICES` list, including UUID entries. The command
prints its URL only after structured service readiness and identity checks.
Ctrl-C or SIGTERM releases only the process groups owned by that command.
A busy port is an error; the CLI does not attach to or stop its owner.

`--output-dir` is a parent directory. Each invocation creates a unique child
containing `deployment.json`, `service.log` and native environment artifacts.

## Run a task with one command

Set `MODEL` and provider credentials first. The example uses
`UNI_ROBOT_AGENT_API_KEY` and `UNI_ROBOT_AGENT_PROVIDER_URL` for a
Responses-compatible provider; alternatively pass `--provider-url` explicitly.

```bash
uni-robot-agent run --integration robodojo --deploy local \
  --task stack_bowls --model "$MODEL" --action-profile direct \
  --no-sam3 --output-dir ./runs
```

This starts and owns only the environment. It does not implicitly start actor
or SAM3. For Hybrid execution, deploy an actor separately and supply
`--action-model-url` plus its `--action-model-checkpoint-sha256`. For SAM3
perception, deploy SAM3 and set `--sam3-url`; otherwise use `--no-sam3`.
Provider and agent options work as usual. Use `--env-gpu` / `--env-port` for the
managed environment.

To connect to an independently prepared episode:

```bash
uni-robot-agent run --integration robodojo --env-url http://localhost:18760 \
  --task stack_bowls --model "$MODEL" --action-profile direct --no-sam3
```

`--env_url`, `--action_model_url`, and `--sam3_url` are aliases.
An environment URL, including one inherited from configuration environment
variables, conflicts with `--deploy local`. External services keep running after
the agent exits, but the episode is finalized.
Each independently deployed environment serves one prepared episode.

TUI connects through `--env-url` only. To use it locally, run `deploy env` in a
separate terminal, then launch `tui` against the printed URL. It does not accept
`--deploy local` or `--monitor`; see [terminal walkthrough](getting-started.md#view-one-task-in-the-terminal).

The defaults are a 10800-second episode timeout and unlimited turns
(`--max-turns 0`).
Installation, startup and episode budgets are separate. The managed parent
supervises the agent process in a separate group and remains alive to clean up on
success, failure, timeout or cancellation. `supervisor.json` records these
lifecycle results separately from the agent process's authoritative `result.json`.
Agent completion is not official task success. Deployment failure does not
fabricate a successful task result.

## Environment profiles and limits

Official RoboDojo uses the pinned clean checkout and frozen task layout.
Only seed 0 is supported. `--layout` and optional `--layout-sha256` select and
verify an alternative frozen layout. The actual task, seed and hash appear in
service identity and run records.

RoboUse recipes are separated by incompatible native dependencies:
`mujoco`, `libero`, `robocasa`, `maniskill_cpu`, `maniskill_gpu`, `genesis`,
and `factory`. Setup defaults to `mujoco`; deployment can resolve an unambiguous
bundled task from prepared runtime catalogs, or use an explicit profile.
Use `--task-dir` for an external RoboUse task. Each backend defines task admission,
native grading, step budgets and camera defaults; all use Direct control. Validate
each runtime profile separately on its target hardware.

RoboLab uses a [dedicated connection workflow](robolab.md) without automatic
deployment. Physical-robot integrations are not part of this public release.
LIBERO tasks use the RoboUse `libero` profile.

Python consumers can reuse the recipe/runtime/launch contracts and owned
`Deployment` handle in `services/deployment/`. CLI signal handlers, process exit
codes, and CLI process entry points are not an embedded SDK API.

## Python ownership

`setup(SetupConfig(...))` prepares a runtime explicitly.
`deploy(DeploymentConfig(...))` starts a prepared runtime and returns the shared
`Deployment` handle. Nest `Session` inside its context; exiting closes only the
owned process group. These Python entry points install no signal handlers and do
not invoke CLI process supervision. See [SDK deployment](sdk.md#local-preparation-and-deployment).

### Monitor alongside deployment

`run --deploy local --monitor` starts its local Web host before environment startup.
The deployment supervisor owns this host; the agent process does not start a second
one. A requested monitor port conflict fails before deploying an environment.
The browser has no authority over service lifetimes, and closing it does not
cancel the run. Existing `--env-url` mode preserves external service ownership.
See [local Web monitoring and directory replay](running.md#local-web-monitor).

## Hybrid models across integrations

A prepared environment advertises its binding and native control capabilities.
Connect a separately deployed actor with a matching explicit profile; installing
an integration does not supply compatible model weights. The
[XPolicyLab bridge](services.md#xpolicylab-bridge) accepts configured observation
and action mappings for any integration. Its pinned upstream checkout is needed
only in the bridge/model deployment, not on each planner machine. Keep the older
pinned Pi0.5 runtime separate. See [Hybrid compatibility](hybrid.md) before
selecting camera, frequency, frame, control-point or delta conventions.
