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
│   │   └── backends/pi05/
│   │       ├── backend.py      # one Pi05Backend
│   │       ├── config.py
│   │       └── openpi_jax.py   # native runtime adapter
│   └── perception/
│       ├── contracts.py
│       ├── client.py           # PerceptionClient
│       ├── codec.py            # PNG/base64 wire conversion
│       ├── routes.py
│       ├── service.py          # PerceptionService
│       ├── backend.py          # PerceptionBackend
│       ├── server.py
│       └── backends/sam3.py
├── integrations/
│   ├── robolab/                 # Direct DROID adapter and native environment host
│   ├── robouse/                 # Direct native backends and deployment recipes
│   └── robodojo/                # spec, prompt, bindings, environment/adapter.py
├── tools/
│   ├── gripper/                 # arm/gripper schemas and high-level motion
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
must match those identities, model/checkpoint and the declared action shape.
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

## Pi0.5 deployment profiles

| Profile | Native runtime | Observation | Action chunk |
|---|---|---|---|
| `robodojo_pi05` | `openpi_jax` | Three cameras, fourteen joint/gripper values | Fifty rows of fourteen joint/gripper targets |

The profile uses `Pi05Backend`, `ActorService` and `ActorClient`. Action bindings own
camera orientation and environment-specific state/action conversions. The runtime
adapters own native model loading, transforms and inference. RoboDojo also
retains its pinned source and checkpoint identity validation.

A compatible checkpoint change is configuration. A different native runtime
or observation/action convention requires an explicit adapter/profile; copying
a service or weakening profile validation is unnecessary. Importing a client,
contract, or CLI help does not import JAX, Torch, SAM3 or a simulator.

## Start services

Install the base harness into each prepared runtime environment, alongside its
native dependencies. Copy and edit the appropriate TOML example:

- [`pi05_robodojo.toml`](../examples/services/pi05_robodojo.toml)
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
`uni_robot_agent.services.perception.server`. Actor TOML selects `backend`, `runtime`,
`profile`, `model_config_name`, `checkpoint`, and optional pinned source/hash
fields. `[http]`, `[batching]` and `[sessions]` configure host, queue and session
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

Environment protocol `"3"` exposes `EpisodeInfo.environment` and
`EnvironmentState`, with tool schema `"7"`. Persisted environment snapshots use
`artifact_type = "environment_snapshot"`, integer `schema_version = 2` and the
`state` field. Upgrade clients and environment services together when changing
wire contracts; existing run artifacts are not rewritten. Actor clients validate
the complete profile before opening a session.

Connect the harness with HTTP URLs, for example:

```text
--env-url http://127.0.0.1:18760
--action-model-url http://127.0.0.1:8601
--sam3-url http://127.0.0.1:8114
```

The ports are deployment examples, not discovery defaults. A pooled environment
uses its allocated full episode URL. Independent environments may share an actor
and perception service through separate clients and actor sessions.

## Environment protocol and tool orchestration

The service boundary is intentionally small. `EnvironmentBackend` implements
native `step_pose`, `step_joints`, `step_native`, capture, preview and finish.
`EnvironmentService.step` checks the expected `state_version`, forwards exactly
one command, records the receipt, and publishes a snapshot. It knows nothing
about whether a caller is trying to reach a pose, close a gripper, or execute a
recovery sequence.

`tools/gripper/motion.py` is the client-side program layer. It reads the
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

For another checkpoint with an existing compatible Pi0.5 profile, write a TOML
configuration. For another model family, implement `ActorBackend` under
`services/actor/backends/` and add its configuration/loading choice to the actor
entrypoint. The native model wrapper can be small:

```python
from uni_robot_agent.services.actor.backend import ActorWork
from uni_robot_agent.services.actor.contracts import ActorInfo


class MyActorBackend:
    def __init__(self, model, info: ActorInfo):
        self.model, self.info = model, info

    def create_session(self):
        return None  # This example model is stateless between predictions.

    def close_session(self, state):
        pass

    def predict_batch(self, requests: list[ActorWork]):
        observations = [request.observation for request in requests]
        return self.model.predict_batch(observations)

    def close(self):
        self.model.close()
```

`info` declares the model/checkpoint identity and complete profile: cameras,
state/action fields, units, horizon, coordinate frame and gripper/image
conventions. The backend returns one action matrix or exception per request in
input order. A stateful model returns per-session state from `create_session`
and consumes `ActorWork.state` during prediction.

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
the new model needs different snapshot/action conversions, then verify profile
compatibility, native batching, ordering, admission and failure recovery using a
fake backend before a native-runtime smoke test.

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
