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
| Native model server | The official NaVILA environment and 8B weights; benchmark `scripts/vlm_server.py` |
| Actor bridge | Python 3.11+, harness service and official TCP client; no Torch model imports |
| Environment service | Python 3.11+ harness adapter plus a private Python 3.10 Isaac Sim 4.1 worker |

Prepare the pinned upstream sources, scenes and weights listed in
[SOURCE.md](../uni_robot_agent/integrations/navila/SOURCE.md). The environment
worker requires the benchmark extension and its custom IsaacLab installed in
that interpreter. The harness package must not be installed in Python 3.10.

Isaac Sim 4.1 does not support RTX 50-series/Blackwell GPUs. Preflight rejects
that hardware before any GPU launch. Migrating to newer Isaac is separate work.
The original NaVILA Torch 2.3 model runtime also needs compatible hardware;
upgrading its dependencies is not silently treated as an equivalent model.

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

## Start the environment and planner

Use a prepared Python 3.11 harness interpreter for the server and a separately
prepared Python 3.10 Isaac interpreter for `--worker-python`:

```bash
python -m uni_robot_agent.integrations.navila.environment.server \
  --native-root /path/to/NaVILA-Bench \
  --worker-python /path/to/isaac-python/bin/python \
  --task episode:0 --seed 0 --run-id navila-example \
  --output-dir runs/navila-example/environment --no-depth
```

The native worker owns reset, physics, sensors and metrics. Its logs are stored
in `native/native.log`; camera files are immutable within its episode directory.
The parent process uses request IDs and never replays a motion after an unknown
acknowledgement. The environment is prepared once; connecting a planner does not
reset it.

For managed deployment, `setup --integration navila` adopts explicit
`--runtime-python`, `--worker-python` and `--native-root` paths, running a read-only
compatibility check. There is no automatic Isaac installation or migration.

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
