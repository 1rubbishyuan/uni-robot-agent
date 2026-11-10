# Go2 navigation with NaVILA

The `navila` integration connects NaVILA-Bench Go2 navigation to the existing
`actor_act` tool. It exposes scene observation, navigation guidance, Hybrid and
finish; it does not expose gripper or direct arm tools. Dataset episodes share
one binding and are selected as `episode:<zero-based dataset index>`.

The model backend is independent of this integration. Another environment can
reuse it by supplying the declared front-camera history and interpreting its
symbolic navigation predictions in its own binding.

## Runtime requirements

Three processes have separate responsibilities:

| Process | Runtime and responsibility |
|---|---|
| Native model server | Official NaVILA model code and 8B weights; benchmark `scripts/vlm_server.py` |
| Actor bridge | Python 3.11+, harness service and official TCP client; no Torch model imports |
| Environment service | Python 3.11+ harness adapter and a separate native Isaac worker |

The environment admits two explicit runtime profiles:

| Profile | Native worker | Source preparation |
|---|---|---|
| `isaac41` (default) | Python 3.10, Isaac Sim 4.1.0 | Clean pinned benchmark and its original IsaacLab fork |
| `isaac51` | Python 3.11, Isaac Sim 5.1.0.0 | Prepared migration and the pinned compatible IsaacLab fork |

Prepare the exact sources, scenes and weights listed in
[SOURCE.md](../uni_robot_agent/integrations/navila/SOURCE.md). Install the selected
IsaacLab and native benchmark dependencies in the worker interpreter. The worker
loads the benchmark's custom RSL-RL controller and imports no harness package.
The harness package must not be installed in Python 3.10.

Default `isaac41` preflight rejects RTX 50-series/Blackwell GPUs before launching
Isaac. `isaac51` is an explicit migration; selecting it does not install Isaac or
upgrade an existing environment. Its physics results must be reported separately
from the original runtime; official benchmark-score equivalence is not claimed.

The model server has its own compatibility requirements. An isolated Blackwell
model runtime with Torch 2.7.0+cu128 and FlashAttention 2.8.3 has loaded the pinned
8B weights and completed native server inference. This establishes model
execution, not Hybrid navigation success or parity with the original Torch 2.3
runtime. The actor bridge does not install this model environment.

## Prepare the Isaac 5.1 migration

Use a clean benchmark checkout at the revision in [SOURCE.md](../uni_robot_agent/integrations/navila/SOURCE.md)
and a new output directory. Prepare the task assets with this structure:

```text
/path/to/VLN-CE-Isaac/
  vln_ce_isaac_v1.json.gz
  matterport_usd/<scene>/<scene>.usd
```

Retain the original Isaac 4.1 Go2 USD and its referenced mesh file together:

```text
/path/to/go2-isaac41/
  go2.usd
  Props/instanceable_meshes.usd
```

The [original asset URLs and checksum](../uni_robot_agent/integrations/navila/SOURCE.md)
identify the required robot asset. Preparing the migration is a CPU operation:

```bash
/path/to/harness/bin/python -m uni_robot_agent.integrations.navila.environment.isaac51 \
  --source-root /path/to/NaVILA-Bench \
  --output-root /path/to/NaVILA-Bench-isaac51 \
  --go2-usd /path/to/go2-isaac41/go2.usd \
  --assets-dir /path/to/VLN-CE-Isaac
```

`--assets-dir` belongs to this preparation command. If omitted, it uses
`isaaclab_exts/omni.isaac.vlnce/assets` in the source checkout. Missing episode
data or the Matterport directory fails before the output is created.

The helper creates a separate adapted source tree and `navila-isaac51.json`.
The manifest records the source revision, migration implementation, generated
Python files, dataset location and digest, and original Go2 assets. Loading the
profile validates these records and the generated code against the pinned source.
Changes require preparing a new directory; the original checkout is preserved.
The worker also verifies the pinned IsaacLab revision and actual import paths.

## Start the independent actor

Start the official model server in its prepared native environment:

```bash
/path/to/navila-model/bin/python /path/to/NaVILA-Bench/scripts/vlm_server.py \
  --model_path /path/to/navila-llama3-8b-8f --host 127.0.0.1 --port 54321
```

Copy [the bridge configuration](../examples/services/navila_8b.toml), set the
upstream endpoint, and replace its checkpoint digest with the identity of your
prepared weights. The official TCP protocol does not expose remote weight or
server-instance attestation: identity is operator-declared. Dedicate the model
server to the bridge; one actor session is admitted at a time.

```bash
python -m uni_robot_agent.services.actor.server --config /path/to/navila-actor.toml
```

The `navila_8b` deployment recipe manages only this bridge. Its setup requires
`--checkpoint-sha256`. `NAVILA_UPSTREAM_HOST` and `NAVILA_UPSTREAM_PORT` may be
provided through explicit `--runtime-env` settings. It does not install or
start the external GPU model. A transport, timeout or decoding failure poisons
the bridge; diagnose the upstream and restart before beginning another session.

The decoder accepts one complete command, including the native model's
`The next action is ...` wrapper. It does not extract a motion from arbitrary
prose or fall back to moving forward. On a decoding failure, the HTTP error's
`details.evidence` retains the exact `native_output` and sampled input-frame
metadata. The actor service also logs this evidence with the admitted
`session_id`, `request_id` and `sequence`; managed deployments write it to their
`service.log`. Capture stderr when starting the bridge directly. Request receipts
retain their existing status and error string, so use the request IDs to correlate
them with the full failure log. No image payloads are duplicated in that evidence.

## Start the environment and planner

For the default `isaac41` profile, use a Python 3.11+ harness interpreter and a
separately prepared Python 3.10 Isaac interpreter for `--worker-python`:

```bash
python -m uni_robot_agent.integrations.navila.environment.server \
  --native-root /path/to/NaVILA-Bench \
  --worker-python /path/to/isaac-python/bin/python \
  --task episode:0 --seed 0 --run-id navila-example \
  --output-dir runs/navila-example/environment --no-depth
```

For `isaac51`, point the server at the generated source tree and its Python 3.11
worker. The optional recording flag retains every native control frame:

```bash
/path/to/harness/bin/python -m uni_robot_agent.integrations.navila.environment.server \
  --native-root /path/to/NaVILA-Bench-isaac51 \
  --worker-python /path/to/isaac51/bin/python --runtime-profile isaac51 \
  --task episode:0 --seed 0 --run-id navila-isaac51-example \
  --output-dir runs/navila-isaac51-example/environment --no-depth \
  --record-native-frames
```

The native worker owns reset, physics, sensors and metrics. Its logs are stored
in `native/native.log`; camera files are immutable within its episode directory.
The parent process uses request IDs and never replays a motion after an unknown
acknowledgement. The environment is prepared once; connecting a planner does not
reset it.

Managed deployment adopts these existing environments through a read-only
compatibility check. Use a distinct cache for the selected runtime:

```bash
uni-robot-agent setup --integration navila \
  --runtime-python /path/to/harness/bin/python \
  --worker-python /path/to/isaac51/bin/python \
  --native-root /path/to/NaVILA-Bench-isaac51 --runtime-profile isaac51 \
  --cache-dir /path/to/navila-isaac51-cache

uni-robot-agent deploy env --integration navila --runtime-profile isaac51 \
  --cache-dir /path/to/navila-isaac51-cache --task episode:0 --seed 0 \
  --run-id navila-isaac51-managed --output-dir runs
```

Setup stores the profile with the adopted paths; deployment verifies and forwards
that profile. A conflicting profile is rejected. Omitting the profile during
initial adoption retains the `isaac41` default. Neither setup nor deployment
installs or silently migrates Isaac.

Use [the Hybrid example](../examples/navila_hybrid.sh) with the environment and
actor URLs and the matching checkpoint digest. The integration itself assembles
the visible tools. A reviewer may allow one complete navigation segment or stop;
it cannot edit base velocities. `--no-vla-review` uses the same execution path.

## Observation, execution and evaluation

NaVILA receives language and RGB history. The low-level Go2 controller additionally
uses proprioception and a LiDAR height map. Reference routes, ground-truth
locations, exact goals and task success are evaluator-private.

One actor action requests a forward move of 25/50/75 cm, a left/right turn of
15/30/45 degrees, or stop. The binding converts movement to a 0.5–1.5 second
velocity segment, executed by the native 50 Hz locomotion controller. Requested
displacement is not measured travel. The physics loop runs at 200 Hz.

The environment captures intermediate frames during segments. Historical frame
IDs, simulation times and state versions travel with immutable snapshots.
The backend selects eight frames uniformly over the episode, padding initial
history with explicitly marked black images. The pinned native capture phase is
0, 0.02, 0.52, 1.02, ... seconds, so its latest history frame can be older than the
current snapshot. Sampling only at actor-call boundaries is not equivalent.

With `--record-native-frames` on the direct environment server, PNGs and
`native/native-frames/index.jsonl` retain tick 0 after warmup and every executed
50 Hz control tick. Each index row includes `native_tick` (also named `tick`),
`simulation_time`, `native_request_id`, `path`, `sha256`, `captured_at` and
`camera`. Repeated frame reads reuse the existing file and index entry.

Execution receipts contain `native_tick_start` and `native_tick_end`: a segment
from tick `a` to `b` executed `b - a` control steps and produced frames `a + 1`
through `b`. Join these ranges to the frame index while retaining the service's
command/execution identities and snapshot endpoints. Protocol request IDs also
count state and frame requests, so use native ticks for motion alignment. Dense
video records and sparse service snapshots keep their respective identities.

Actor stop returns control to the planner. It neither ends the episode nor
establishes arrival. Planner finish declares navigation STOP and computes the
private native success criterion (STOP plus goal-radius distance), SPL and other
metrics. Agent errors, timeouts and service cleanup do not declare navigation
STOP. A fall or exhausted 2,500-step budget remains a terminal failure.
This explicit Hybrid finish differs from the native route-only evaluator.

Preserve the full route instruction for the initial native-model comparison.
Agent-written subgoals are a separate experiment. CPU contract tests validate
parsing, framing, history and lifecycle behavior; they do not establish GPU
compatibility or navigation success rates.
