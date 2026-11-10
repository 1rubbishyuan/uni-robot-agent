# Services

For resource preparation and owned local services, see [Local deployment](deployment.md).

uni-robot-agent separates environment execution, action models and perception into
independent HTTP services. Typed clients and routes keep native simulator,
hardware and GPU dependencies outside the agent process. The shared stack uses
HTTPX, FastAPI and Uvicorn; the MCP bridge and provider gateway reuse its
transport and hosting utilities. The CLI is `uni-robot-agent`; Python modules use `uni_robot_agent`.

Prepare only the services needed for the selected action profile:

| Mode | Required services |
|---|---|
| Direct with `--no-sam3` | Prepared environment episode |
| Direct with segmentation | Environment and SAM3 perception |
| Hybrid | Environment and compatible actor; perception is optional; reviewer requirements depend on the binding |

Use [integration setup](integrations.md) to choose an environment and
[running experiments](running.md) to configure the agent.

## Classes and ownership

| Layer | Shared implementation | Domain responsibility |
|---|---|---|
| Wire models | Pydantic | Each domain's `contracts.py` defines its requests, replies and identity checks |
| HTTP client | `services/http.py:HttpTransport` | Domain clients expose typed operations and interpret failures |
| HTTP routes | FastAPI `APIRouter` | Each `routes.py` validates inputs and calls its service |
| HTTP host | `services/hosting.py:create_app`, `HttpServer`, `serve` | Launchers provide resources, bind address and shutdown callbacks |
| Native work | `services/batching.py:BatchWorker` for model inference | Services own admission, queues and lifecycle; native backends implement computation |

`HttpTransport` owns connection reuse, finite request/connect/pool timeouts,
complete response reads, status errors, closing and optional trace propagation.
It performs no retries and follows no redirects. Internal service clients ignore
environment proxy settings; `ModelClient` explicitly enables them for provider
requests. Traces record sanitized URLs without credentials or query strings.
`HttpCallError.response` retains the original HTTPX response, alongside the
existing status, body and headers. Provider gateways and review clients receive
the session tracer: inbound HTTP spans parent outbound HTTP spans, and outbound
requests carry the new hop's `traceparent`. Without an incoming trace, an enabled
tracer starts one. A disabled tracer does not create spans or trace headers.

The host limits request bodies, propagates trace context and shuts down through
Uvicorn's graceful drain. Blocking native work stays outside the event loop.
RoboDojo continues to execute Isaac operations on its owner thread; the owner
queue must remain pumped while accepted HTTP requests and native shutdown drain.
Background startup has a finite deadline. On timeout, the host cancels async
startup and waits a bounded time for lifespan cleanup. If readiness races the
timeout, Uvicorn retains ownership of listener shutdown and request draining.
A synchronous callback that blocks the event loop cannot be forcibly cancelled;
the timeout releases its still-unstarted socket and reports that cleanup remains
active. Lifespan startup callbacks must therefore use cancellable async work.
Each `HttpServer` instance is single-use.

```text
uni_robot_agent/
├── services/
│   ├── http.py                 # HttpTransport
│   ├── hosting.py              # create_app, HttpServer, serve
│   ├── errors.py
│   ├── batching.py             # bounded BatchWorker
│   ├── mcp/
│   │   ├── results.py         # complete tool-result encoding
│   │   └── http.py            # JSON-RPC and HTTP/SSE lifecycle
│   ├── environment/
│   │   ├── contracts.py
│   │   ├── client.py           # EnvironmentClient
│   │   ├── routes.py
│   │   ├── service.py          # EnvironmentService
│   │   ├── backend.py          # EnvironmentBackend native adapter contract
│   │   ├── primitive.py        # one-command physical execution
│   │   └── snapshots.py
│   ├── actor/
│   │   ├── contracts.py
│   │   ├── client.py           # ActorClient
│   │   ├── routes.py
│   │   ├── service.py          # ActorService
│   │   ├── backend.py          # ActorBackend, ActorWork
│   │   ├── images.py
│   │   ├── server.py           # configured model loading and serving
│   │   ├── registry.py         # lazy backend/config/deployment registration
│   │   └── backends/
│   │       ├── openpi/         # one OpenpiBackend, recipe-selected native runtime
│   │       ├── gr00t/          # official GR00T policy and processor
│   │       ├── navila/         # official external TCP model bridge
│   │       └── xpolicylab/     # official external WebSocket model bridge
│   └── perception/
│       ├── contracts.py
│       ├── client.py           # PerceptionClient
│       ├── codec.py            # PNG/base64 wire conversion
│       ├── routes.py
│       ├── service.py          # PerceptionService
│       ├── backend.py          # PerceptionBackend
│       ├── server.py
│       └── backends/sam3.py
├── integrations/               # specs, tool compositions, physical bindings and native adapters
│   ├── robodojo/, robouse/, robolab/
│   ├── dexjoco/                 # dexterous arm/hand
│   └── behavior/, isaaclab_arena/, navila/  # mobile embodiments
├── tools/
│   ├── gripper/                 # arm/gripper schemas and high-level motion
│   ├── hybrid/                  # typed proposals, optional review and bounded execution
│   └── scene/                   # snapshot localization and geometry
└── provider/                   # model APIs, retries, adapters and wire traces
```

Each domain has a client and routes because the operations and execution
semantics differ. It does not have another HTTP implementation. Model backends
have no network superclass, socket server or facade.

## Protocols and failure recovery

| Service | Important routes | State ownership |
|---|---|---|
| Environment | `GET /info`, `/snapshots/latest`, `/snapshots/{id}`, `/records`, `/artifacts/{id}`, `/requests/{id}`, `/outcome`; `POST /steps`, `/previews`, `/captures`, `/finish` | Prepared episode, native state, immutable snapshots, ordered execution receipts |
| Actor | `GET /healthz`, `/info`; `POST /sessions`; `GET`/`DELETE /sessions/{id}`; `POST /predict`; `GET /sessions/{id}/requests/{id}` | Model identity, explicit inference sessions, RNG, sequence and inference receipts |
| Perception | `GET /healthz`, `/info`; `POST /segment` | Model instance, bounded inference queue and embedding cache |

Environment routes can be mounted under a prepared episode prefix. An
`EnvironmentClient` connects to that full episode URL. Commands include the
episode, request and sequence identities; the service distinguishes rejected,
completed and uncertain execution. A lost response must be reconciled through
the receipt. Closing a client releases its connections; ending the episode is
an explicit `finish` operation.

`ActorClient` checks `/info`, including protocol, model identity, profile and
an optional expected checkpoint hash, then creates an explicit HTTP session.
Every prediction includes `session_id`, `request_id` and `sequence`. The reply
must match those identities, model/checkpoint and the declared prediction type,
shape or label/parameters. Actor v3 supports numeric chunks and symbolic
predictions; optional camera history retains source frame identities and timing.
One session permits one accepted inference at a time.

Actor failures have explicit outcomes:

- A validation, sequence or admission rejection reports `accepted=false` and
  does not advance the session.
- A lost or invalid prediction response raises `ActorUncertain`. Call
  `reconcile()` to inspect the receipt before doing anything else. No request
  is automatically replayed.
- A native inference failure poisons the session, since RNG or model state may
  already have changed. A new session is an explicit new policy sequence.
- Missing/expired sessions, including a server restart, fail rather than
  silently recreating state. Receipts are bounded; an unavailable receipt is
  insufficient evidence to replay an inference.

`DELETE /sessions/{id}` closes an idle actor session or marks an active one
for closure after accepted inference finishes. `ActorClient.close()` uses that
operation and releases its HTTP connections.
An already-expired `session_missing` response (404/410) counts as closed; other
HTTP failures still propagate, and the client closes its transport in either case.
Default limits are configurable
under `[sessions]`: 1,024 sessions, 1,800-second idle timeout, 3,600-second receipt
retention, the latest 128 receipts, and 4,096 accepted request identities per
session. Retention is bounded by both session lifetime and receipt limits.

Perception protocol v2 carries an encoded image, `min_score`, and a discriminated
`query`: `{"kind": "text", "text": "red cup"}` or
`{"kind": "point", "point": [row, column]}`. The point is one positive pixel,
inside the source image. Upgrade the service and client together; v1 requests
and responses are not supported. Perception has no episode mutation or RNG
session protocol.

Results report `matched`, `no_match`, `below_threshold`, or `empty_mask`.
Only `matched` contains a mask: a PNG/base64 payload with its source-image
`[height, width]` shape. `box_xyxy`, when available, uses source-image pixel
coordinates; point results may omit it. Scores are finite values in `[0, 1]`,
but text and point heads do not share a calibrated confidence scale. Invalid
images/points are request errors; malformed model output is a backend error.

The backend and service exchange `SegmentInput` and in-memory
`SegmentationResult` objects. `codec.py` owns image/mask encoding, and
`PerceptionClient` returns boolean NumPy masks. Snapshot identity, calibrated
geometry and geometry quality remain in `tools/scene`, outside SAM3.

## Batching and concurrency

HTTP concurrency and model batching are separate. Each actor or perception
service has one bounded queue and one native batch worker. Admission happens
before waiting on a future, so a busy model does not create an unbounded pool of
waiting inference threads. Health and information routes remain independent of
the model's work queue.

Different actor sessions can form one compatible native model batch; requests
within a session stay ordered. The OpenPI adapter preserves the seeded JAX RNG
sequence per session.
SAM3 retains text batching, image-embedding reuse and serialized point prompts
where required by the pinned runtime.
Its single `Sam3Backend` prepares requests, runs the text and point paths, and
converts native outputs into `SegmentationResult`. Model state and output
conversion stay together, as they do in each actor runtime; the service owns
only queueing and lifecycle. Prepared requests retain their input position and
image size so mixed image shapes return masks and boxes in the original frame.
SAM3 0.1.4 keeps one image-feature cache slot, owned by that worker. Each prompt
gets separate state dictionaries while sharing the cached image tensors; point
inference releases its transient predictor state even when inference fails.
All native inference, including batched text, runs with autograd disabled.
Single and batched text results share candidate selection: first highest score
(stable input order breaks ties), then threshold, then empty-mask rejection.
An empty top mask does not select a lower-scoring candidate. Batched text also
discards zero-score candidates, matching the pinned single-image processor.

Actor and SAM3 services have separate queues and can run concurrently. The
harness executor still determines which tools may overlap, and the environment
still serializes physical state changes. There is no shared lock around all
HTTP clients. MCP dispatch starts the SSE response before executing a tool, so
a long action does not prevent the SDK from dispatching an independent call.

The MCP bridge requires a string or integer request ID for `initialize`, `ping`,
`tools/list`, and `tools/call`. Only the `notifications/initialized` lifecycle
notification is accepted without an ID (HTTP 202, empty body). Other ID-less
messages, including tool calls and unsupported cancellation notifications, are
rejected with HTTP 400 and no JSON-RPC response. The bridge does not implement
generic JSON-RPC fire-and-forget execution or tool cancellation.

## OpenPI deployment profiles

The `openpi` backend runs native OpenPI/JAX diffusion policies through one implementation.
Model recipes supply pinned source and checkpoint identities, the training
configuration, image mapping and the complete physical observation/action profile.
Integration bindings validate those physical meanings and compile environment
commands. Upstream policy transforms retain normalization, tokenization, padding
and action decoding.

| Recipe | Observation | Action chunk | Native inference |
|---|---|---|---|
| `robodojo_pi05` | Three cameras and fourteen X5 joint/gripper values | 50 × 14 absolute joint/gripper targets | True batches with per-session noise |
| `dexjoco_pick_bucket` | Two cameras, measured TCP xyz/wxyz and sixteen finger positions | 30 × 22 absolute TCP xyz/rotation-vector and finger targets | Single observation per inference call |
| `behavior_r1pro_pi05` | Three cameras and native 61D proprioception; original transform selects 23D state | 32 × 23 joint/gripper targets and normalized base commands | Single observation per inference call |

Recipes can map named physical fields into one or several model input arrays
with `observation_mapping`; omitted mapping retains the `state` key. Every
observation field must appear exactly once. `prompt_key`, `image_container`,
`image_keys` and `image_layout` describe the remaining wire layout without
embedding robot-specific key names in the backend. These mappings do not perform
physical frame or unit conversions; those remain in the integration binding.

All OpenPI recipes instantiate `OpenpiBackend`, `ActorService` and `ActorClient`.
Observation width, physical action width and the model's internal sampling width
are independent: X5 uses a 32-wide latent space for 14-wide physical actions;
DexJoCo single-arm uses a 32-wide latent space for 22-wide physical actions.
The loaded training configuration supplies the internal sampling dimensions.
The native model must accept explicit diffusion noise; Pi0-FAST uses a different
sampling API and is not admitted by this backend.

The pinned X5 batch API and single-observation OpenPI API are explicit runtime
capabilities. A single-observation recipe requires `max_batch_size=1`; separate
sessions still own separate noise sequences. Unsupported batching is rejected.
Different native batch shapes can produce floating-point differences, so
regressions compare each execution mode with its matching reference.

`backend="pi05"` and its `xpolicylab_root` option remain compatibility aliases for
the original RoboDojo recipe. New configurations use `backend="openpi"` and
`native_root`. The old `backends.pi05` Python modules have moved to
`backends.openpi`; X5 profile helpers now belong to `integrations.robodojo`.
RoboDojo's legacy binding retains its model/config/seed and image contract.
Model IDs now include effective source, preprocessing and sampling configuration;
clients establish fresh sessions after upgrading the service. Checkpoint hashes
and the legacy physical profile keep their existing meanings.

A compatible checkpoint needs configuration and a matching binding, not another
backend. A different model serving protocol can implement `ActorBackend`, as the
existing XPolicyLab bridge does. Importing clients, configuration or CLI help
never loads JAX, Torch or a simulator.

## Backend configuration and preparation

`registry.py` resolves a lazy backend definition. Its configuration module owns
backend-specific options and service limits; its deployment module owns native
source, weights, dependencies and launch configuration. Shared deployment retains
cache, process and shutdown ownership. Adding another environment for an existing
model protocol requires a recipe/profile and integration binding, not a duplicate
backend or another branch in the process manager.

| Backend | Model boundary | Managed preparation |
|---|---|---|
| `openpi` | Native OpenPI/JAX; recipe mappings preserve upstream transforms | Pinned source/environment and supported checkpoint sources; R1Pro weights supplied explicitly |
| `gr00t` | Official `Gr00tPolicy` and processor, numeric chunks | `arena_g1_gr00t`; separate Python 3.12 model runtime |
| `navila` | Official TCP server, symbolic navigation from image history | `navila_8b` prepares the bridge; GPU model server is externally prepared |
| `xpolicylab` | Official WebSocket server, explicitly mapped numeric profile | External upstream and direct bridge launch; no managed deployment |

GR00T, NaVILA and XPolicyLab admit one active session and batch size one. OpenPI
limits depend on the selected runtime capability. Omitted batch/session limits
receive backend-compatible defaults; explicit incompatible values are rejected.
The backend decodes model output but never executes robot commands. Native runtime
and GPU restrictions are documented in [Arena](isaaclab_arena.md),
[NaVILA](navila.md) and [BEHAVIOR](behavior.md); source/configuration validation is
not evidence of a successful native rollout.

## Start services

Install the base harness into each prepared runtime environment, alongside its
native dependencies. Copy and edit the appropriate TOML example:

- [`pi05_robodojo.toml`](../examples/services/pi05_robodojo.toml)
- [`openpi_dexjoco_single.toml`](../examples/services/openpi_dexjoco_single.toml)
- [`openpi_behavior_r1pro.toml`](../examples/services/openpi_behavior_r1pro.toml)
- [`gr00t_arena_g1.toml`](../examples/services/gr00t_arena_g1.toml)
- [`navila_8b.toml`](../examples/services/navila_8b.toml)
- [`xpolicylab_x5.toml`](../examples/services/xpolicylab_x5.toml)
- [`sam3.toml`](../examples/services/sam3.toml)

From the repository root:

```bash
ACTION_MODEL_PYTHON=/absolute/path/to/actor/bin/python \
  bash scripts/deploy/services/action_model/start_service.sh \
  --config /absolute/path/to/pi05.toml

PYTHON=/absolute/path/to/sam3/bin/python \
  bash scripts/deploy/services/perception/start_sam3.sh \
  --config /absolute/path/to/sam3.toml
```

The equivalent Python entrypoints are `uni_robot_agent.services.actor.server` and
`uni_robot_agent.services.perception.server`. Actor TOML selects `backend` and
its native or bridge options, such as runtime, source/checkpoint identity or
upstream endpoint. A string `profile` selects a packaged recipe; its backend must
match the selected backend. Recipes without an explicit backend retain legacy
OpenPI behavior. A full profile table can accompany an explicit model configuration
where supported. Recipes with a required working directory must be launched there;
managed deployment sets it automatically.
`[http]`, `[batching]` and `[sessions]` configure host, queue and session
limits. CLI arguments override the corresponding TOML values; see each
entrypoint's `--help`. The actor's `--identity-output` writes the resolved
identity for launchers and episode configuration.

Perception TOML selects `backend = "sam3"`, `checkpoint`, and an optional
`cuda_device`. Its `[http]` table accepts `host`, `port` and `max_request_bytes`
(128 MiB by default); `[batching]` accepts `max_batch_size`, `max_wait_ms` and
`max_queue_size`. Configuration is validated before loading the GPU model,
including finite wait times and a queue at least as large as one batch.

Native environment entrypoints remain in `integrations/<integration>/environment/`.
They construct `EnvironmentService` and mount its shared routes after preparing
the native environment. Use the [existing simulator launchers](running.md#scripts)
for prepared simulation episodes.

Environment protocol `"4"` exposes `EpisodeInfo.environment` and
`EnvironmentState`, with tool schema `"8"`. Persisted environment snapshots use
`artifact_type = "environment_snapshot"`, integer `schema_version = 2` and the
`state` field. Upgrade clients and environment services together when changing
wire contracts; existing run artifacts are not rewritten. Actor clients validate protocol and checkpoint identity before opening a session.
The integration then validates the complete actor/environment profile before
inference or physical commands; setup failure closes the owned actor session.

Connect the harness with HTTP URLs, for example:

```text
--env-url http://127.0.0.1:18760
--action-model-url http://127.0.0.1:8601
--sam3-url http://127.0.0.1:8114
```

The ports are deployment examples, not discovery defaults. A pooled environment
uses its allocated full episode URL. Independent environments may share model
services only when the selected backend supports their concurrency and session
requirements; an exclusive upstream bridge cannot serve concurrent episodes.

## XPolicyLab bridge

The optional `xpolicylab` backend connects an independently deployed
[XPolicyLab](https://github.com/XPolicyLab/XPolicyLab) policy to the actor HTTP
API. The harness owns fresh capture, review and bounded execution. The upstream
model adapter owns preprocessing, normalization and decoding. One selected model
serves one episode. Checkpoint compatibility must be validated for that episode's
observation and control semantics; equal vector dimensions are insufficient.

Actor protocol **`uni_robot_agent.actor.v3`** carries a typed `prediction` reply.
Upgrade clients and services together and restart them with fresh sessions; v2
HTTP clients cannot reuse the new wire contract or old session IDs. XPolicyLab
continues to emit numeric chunks with unchanged physical meanings.
`ActorProfile.observation_fields` and
`action_fields` independently list scalar names, units, semantics, optional frame,
control point, delta reference and bounds. The profile also declares cameras,
control frequency, action horizon and image convention. There is no required arm,
gripper or equality between observation and action dimensions.

| Configuration | Responsibility |
|---|---|
| `[profile]` and its field arrays | Complete semantic contract validated by the integration binding |
| `[camera_mapping]` | Upstream camera key to profile camera name, covering each view once |
| `[observation_mapping]` | Upstream state key to ordered observation field names |
| `[action_mapping]` | Upstream action key to ordered action field names |

Mappings must cover every declared field exactly once. The bridge does not select
an integration, change units or frequency, interpret deltas, clip values, or load
robot drivers. Output dictionaries must match the declared action keys and widths.
The X5 example remains a deployment configuration, not a backend restriction.

For existing bridge configurations, replace top-level `action_horizon`, `cameras`,
`action_semantics`, `control_frequency_hz` and `gripper_convention` with the full
profile and the three explicit mappings in the updated example. There is no
silent v1 fallback. The pinned Pi0.5 backend keeps its legacy profile, transforms
and binding-side clipping as a separate numeric compatibility path under actor v3.

The upstream adapter must condition on the current instruction and generate a
new proposal from each supplied observation. Policies that return an old queued
chunk or require uninterrupted action-history execution cannot be declared
compatible: hybrid may discard a suffix, apply a correction or execute Direct
actions between predictions. Language, freshness and exclusivity are operator
declarations; verify them against the chosen adapter and checkpoint.

### Deployment

Prepare the upstream checkout and the model's own environment/weights using its
instructions. Both client sources and server must use revision
`6baccd57ff958570d0db1588370289ee36c8bb70`. Keep this checkout separate from the
existing pinned Pi0.5 runtime. From the prepared XPolicyLab checkout, start its
official server with the chosen policy's deployment YAML:

```bash
python -m client_server.ws.model_server \
  --config-path /absolute/path/to/policy-deploy.yml --host 127.0.0.1 --port 19000
```

In the harness bridge environment, install `uni-robot-agent[xpolicylab]` (or
`pip install -e '.[xpolicylab]'` from this source checkout). Copy
[`xpolicylab_x5.toml`](../examples/services/xpolicylab_x5.toml) and replace the
model/config labels, checkpoint hash, horizon, path and camera mapping with the
validated deployment values. The example's all-zero checksum is a placeholder.
Then start the bridge:

```bash
python -m uni_robot_agent.services.actor.server \
  --config /absolute/path/to/xpolicylab_x5.toml \
  --identity-output /absolute/path/to/actor-identity.json
```

The bridge uses the pinned official WebSocket client, checks the client import
closure's source hashes, and calls atomic `INFER` so observation update and
prediction hold the upstream model lock together. That narrow compatibility
boundary uses upstream client internals; upgrading XPolicyLab requires reviewing
and updating its pinned hashes. Native model libraries are not imported by the
bridge. The upstream process remains responsible for loading its own weights.

The checkpoint SHA256 and model/config labels are deployment-owned assertions;
the server handshake does not attest them. Client source checks also do not
verify the running server's revision. The server instance ID only detects a
process restart during reconnect. Deploy and verify the matching server yourself.

Connect the harness to the bridge's **HTTP** URL, using the checksum from your
deployment. Configure planner/reviewer credentials as in [Running](running.md):

```bash
uni-robot-agent run --integration robodojo --model "$MODEL" \
  --provider-url "$PROVIDER_URL" \
  --env-url http://127.0.0.1:18760 --no-sam3 \
  --action-profile hybrid --action-model-binding robodojo_x5_joint \
  --action-model-url http://127.0.0.1:8601 \
  --action-model-checkpoint-sha256 "$CHECKPOINT_SHA256"
```

RoboDojo hybrid retains its default reviewer. A Messages planner needs explicit
Responses-compatible reviewer settings; `--no-vla-review` explicitly disables
review. The SDK selects the same behavior through `SessionConfig`:

```python
integration_options = {
    "action_profile": "hybrid",
    "action_model_binding": "robodojo_x5_joint",
    "action_model_checkpoint_sha256": checkpoint_sha256,
}
```

### Ownership and recovery

Use one bridge and one exclusive upstream process per episode, with
`max_batch_size=1` and `max_sessions=1`. Do not connect other evaluators or bridges
to that upstream: its model state is global, and the bridge cannot enforce
exclusivity across processes. Open/reset happens when the actor session is
created, and normal close invokes `trial_end` before disconnecting. A successfully
closed session releases its active slot while bounded receipts remain available.
A healthy bridge can then admit a new sequential episode; concurrent episodes
remain unsupported.

Reset, inference, invalid output, or shutdown failures make the backend unusable
for new work. After an uncertain or failed call, stop this run and restart the
upstream and bridge together before a fresh episode. Do not only clear client
state or reconnect another session: a timed-out upstream operation may still be
running. HTTP request receipts remain available through the existing API; the
upstream client's transport retry reuses the same request ID against the pinned
server's deduplication cache. It never authorizes replaying physical commands.

Full action chunks remain in the archive. FK preview covers only the first
`min(H,50)` steps; execution stays within `min(H,40)` steps, and corrections stay
within five steps. Every following chunk captures a fresh observation.

## Environment protocol and tool orchestration

The service boundary is intentionally small. `EnvironmentBackend` implements
native `step_pose`, `step_joints`, `step_native`, capture, preview and finish.
`EnvironmentService.step` checks the expected `state_version`, forwards exactly
one command, records the receipt, and publishes a snapshot. It knows nothing
about whether a caller is trying to reach a pose, close a gripper, or execute a
recovery sequence.

`tools/gripper/motion.py` owns Direct motion programs, while
`tools/hybrid/execution.py` owns the independent bounded actor-prefix loop. It reads the
latest typed state, plans bounded waypoints, calls `EnvironmentClient.step`
repeatedly, checks feedback, and stops on tolerance, divergence, native
termination, or a step budget. This is why high-level orchestration is in the
tools package while physical execution remains in the service. Scalar-gripper physical commands use `/steps`; optional device backends use
`/device-steps` and the independent `/interrupts` channel. There are no move,
rotate, gripper, hold, or action-prefix routes. A prefix is a tool-side loop that sends
one `/steps` request per command, so a failed or uncertain acknowledgement can
be reconciled without hiding multiple physical actions behind one service call.

`RemoteArtifactMirror` downloads and verifies service artifacts into the
session-owned `ArtifactStore`. Prompt, actor, perception and training code use
the same image files. `local_path()` waits for that artifact's write to finish;
`relative_path()` supplies an archive reference without changing the environment
wire reference or snapshot identity. The store has no robot or provider logic.

## Add an actor backend

For another checkpoint using a compatible model protocol, write a recipe or TOML
configuration and validate its physical profile against an integration binding.
For another model protocol, implement `ActorBackend` under
`services/actor/backends/`, provide `config.resolve_config` and `service_limits`,
and register a `BackendDefinition`. Optional managed preparation belongs in that
backend's deployment module. The native model wrapper can be small:

```python
from uni_robot_agent.services.actor.backend import ActorWork
from uni_robot_agent.services.actor.contracts import ActorInfo, NumericChunk


class MyActorBackend:
    def __init__(self, model, info: ActorInfo):
        self.model, self.info = model, info

    def create_session(self):
        return None  # This example model is stateless between predictions.

    def close_session(self, state):
        pass

    def predict_batch(self, requests: list[ActorWork]):
        observations = [request.observation for request in requests]
        matrices = self.model.predict_batch(observations)
        return [NumericChunk(actions=matrix) for matrix in matrices]

    def close(self):
        self.model.close()
```

`info` declares model/checkpoint identity and a complete numeric or symbolic
profile: cameras, observations, physical semantics, optional history requirements,
and either action fields/timing or labels/parameters. Return one `NumericChunk`,
`SymbolicPrediction` or exception per request in input order. Numeric output must
match the full declared horizon; symbolic output must match a declared label and
its exact parameter fields. A stateful model returns per-session state from
`create_session` and consumes `ActorWork.state` during prediction. Keep native
preprocessing, normalization and history selection in the backend; frame/unit
conversion and physical command compilation remain in integration bindings.

Compose it with the existing service and routes:

```python
from uni_robot_agent.services.actor.routes import build_router
from uni_robot_agent.services.actor.service import ActorService
from uni_robot_agent.services.hosting import create_app, serve

service = ActorService(backend, max_batch_size=4, max_queue_size=64)
app = create_app(build_router(service), on_close=service.close)
serve(app, host="127.0.0.1", port=8601)
```

No new HTTP client, facade or server is needed. Add an action binding when
the new model needs different observation/prediction conversions, then verify
profile compatibility, batching, ordering, admission and failure recovery using
a fake backend before a native-runtime smoke test. Keep tests under `tests/`;
fixture passes alone do not demonstrate native policy performance.

## MCP and provider delivery

`services/mcp/http.py` provides the Codex HTTP/SSE bridge;
`agents/claude_code/tools.py` registers in-process Claude SDK tools. Both use
`services/mcp/results.py` to encode images, text, background job handles and
`isError`. The Claude bridge returns native MCP results and carries the SDK's
`tool_use_id` through a hook-only argument that is removed before validation
and execution. Model-visible tool schemas are unchanged.
A failed tool call is an MCP error even without an error message;
an accepted background job or an unsuccessful episode outcome is not itself a
tool error. Each transport owns dispatch and its existing event source, so the
shared encoder neither executes tools nor emits events. Tool schemas and
execution policies stay in `tools/` and `execution/`.

`provider/gateway.py` owns model request preparation, retries, response protocol
conversion and trace persistence. It uses the common `HttpTransport` and
`HttpServer`; retries remain provider-specific. Responses are fully buffered
before delivery, including detection of truncated bodies and failed streams.
Prepared response bindings commit only after the final ASGI send succeeds and
are revoked on failed or cancelled delivery. Server-side send success is not
an application-level acknowledgement. Gateway shutdown drains in-flight
requests and callbacks before traces are considered final; a drain timeout is
reported and may be followed by another shutdown attempt.

## Agent plugins

Agent implementation lives in `uni_robot_agent/agents/`. See
[Agent integration](agents.md) for the extension contract and the native
Claude Messages route. Session owns endpoint and artifact lifetimes; each
agent supplies its protocol, upstream adapter, response binding and record
finalizer. Existing Responses adapters continue to receive Responses requests.
