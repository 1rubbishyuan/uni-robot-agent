# Model providers

Provider profiles describe a service endpoint and its wire protocol, independently
of the planner SDK and model name. A model identifier such as `qwen3.8-flash`
does not identify the hosting service or select an adapter automatically.

## Profiles

| Profile | Client input | Upstream format |
|---|---|---|
| `passthrough.responses` | Responses | Responses |
| `passthrough.messages` | Messages | Messages |
| `aliyun.chat_completions` | Responses | Chat Completions |
| `aliyun.gemini_chat_completions` | GenerateContent | Chat Completions |
| `aliyun.messages` | Messages | Messages |
| `tencent.responses` | Responses | Responses |
| `volcengine.responses` | Responses | Responses |
| `moonshot.chat_completions` | Responses | Chat Completions |
| `deepseek.chat_completions` | Responses | Chat Completions |
| `minimax.chat_completions` | Responses | Chat Completions |
| `omnirouter.chat_completions` | Responses | Chat Completions |
| `omnirouter.messages` | Responses | Messages |

Codex declares Responses input; Claude Code declares Messages input. An omitted
`--provider-profile` resolves to `passthrough.responses` or `passthrough.messages`
respectively, and the resolved name is recorded in configuration. Explicit
incompatible combinations are rejected before connecting to services.

Experimental OpenCode defaults to `aliyun.chat_completions` with Qwen thinking
disabled; Codex retains that profile's existing thinking default. Gemini CLI
defaults to `aliyun.gemini_chat_completions`, also with thinking disabled.
See [External CLI backends](external-cli-backends.md) for compatibility limits.
Changing a Google base URL alone does not make Gemini CLI speak Chat Completions.

`aliyun` targets Alibaba Cloud Model Studio. It retains the tested Qwen parameter
rules, including the model-specific Qwen3.8 Messages effort mapping. It is not
a generic adapter for every model hosted by Alibaba or every service hosting
Qwen. `volcengine` targets Ark; `moonshot` targets Kimi's service. Supply the
correct service URL and credentials separately. Passthrough preserves payloads;
it does not discover or guarantee the remote endpoint's capabilities.

`tencent.responses` targets Tencent TokenHub's Responses endpoint, including
HY4. It preserves reasoning fields, tool-call identifiers, history and raw
responses. Inline PNG images are encoded as lossless WebP without changing
pixels or dimensions; other image formats and remote URLs pass through. It
uses the shared provider credentials and HTTP transport, with no Tencent SDK
or native robot dependency. This profile requires Responses input; it does not
implement Tencent's other APIs or a Claude Code Messages adapter. See the
[Tencent configuration example](running.md#tencent-tokenhub-and-hy4).

The Hybrid reviewer uses Responses input independently of the main planner.
Its adapter choices come from the same registry filtered for Responses. It can
inherit a compatible Responses planner's endpoint/profile. For a Messages
or GenerateContent planner, an enabled reviewer needs an explicit `--vla-review-base-url` and
`--vla-review-adapter`. When review availability depends on the prepared action
binding, the check runs during assembly before model requests. A disabled
reviewer opens no model client.

An enabled Hybrid reviewer requires function calling with a named tool choice
and `parallel_tool_calls=false`. Its `submit_review` schema uses the existing
Responses conversion path; compatibility includes the selected model's reasoning
mode. A rejected provider request fails before motion and retains its original
response in the actor-session archive. See [Hybrid review evidence](hybrid.md#reviewer-decision-contract-and-evidence).

## OmniRouter with Codex

OmniRouter's Claude and Gemini routes do not implement the Responses API used
by Codex. Select an explicit conversion profile alongside the service URL and
credentials. Both profiles work with `--interaction-mode code_as_policy` and
retain the existing robot API and execution lifecycle.

Append these options to your configured `run` or `tui` command:

```sh
# Claude Opus 5.5 through native Messages
--agent codex --model claude-opus-5-5 --reasoning-effort medium \
  --provider-url https://omnirouter-api.cc/v1 \
  --provider-profile omnirouter.messages --codex-web-search disabled \
  --interaction-mode code_as_policy

# Gemini 4 Argon through Chat Completions
--agent codex --model gemini-4-argon-medium --reasoning-effort medium \
  --provider-url https://omnirouter-api.cc/v1 \
  --provider-profile omnirouter.chat_completions --codex-web-search disabled \
  --interaction-mode code_as_policy
```

Provide the matching route's credential through `UNI_ROBOT_AGENT_API_KEY`.
The Gemini model suffix selects OmniRouter's `low`, `medium`, or `high` route;
it must match the requested reasoning effort. The bare `gemini-4-argon` name
is not an equivalent OmniRouter route. Gemini requests use non-streaming JSON
upstream because this service can return an empty SSE response; Codex still
receives Responses events after the gateway buffers the complete reply.
Omni Gemini does not reliably consume images inside Chat tool results. The
adapter keeps the result acknowledgements together and places their images in
a following user message, labelled by the original tool-call IDs. Text receipts
and image bytes are preserved.

The Claude adapter sends adaptive thinking and native `output_config.effort`.
It converts function/custom tools, tool results and images, and retains native
thinking/signature blocks by response and tool-call identity within one live
provider session. It never invents signatures. Restart/resume of signed history
across provider sessions is unsupported; start a fresh session. Native search
and forced/named tool choice are unsupported, so this profile cannot serve as
the Hybrid reviewer. Use a separately configured compatible reviewer profile.
Initial developer instructions join the native system prompt; later developer
instructions are explicitly labelled user content because Messages has no
mid-conversation developer role.

Validation covers actual Codex code execution, native image reading and a
subsequent tool-result roundtrip, with `identity` context. This is provider
compatibility evidence, not a robot benchmark or validation of long-run context
compaction. Service model availability and reasoning behavior remain properties
of the selected OmniRouter route.

## Ownership and extension

Paths are relative to `uni_robot_agent/provider/`.

| Module | Responsibility |
|---|---|
| `contracts.py` | Request/response values, provider resource sessions, client protocol and SDK binding interfaces |
| `registry.py` | Profile names, compatible client/upstream pairs, adapter factories and validation |
| `client.py` | Context preparation, upstream requests, bounded retries and provider-session cleanup |
| `gateway.py` | Local HTTP ingress, buffered forwarding, delivery and binding commit/revocation |
| `protocols/` | Responses, Chat Completions and Messages parsing, protocol errors, SSE and Messages keepalive |
| `conversions/` | Responses/Chat/Messages translation and reversible tool-name mapping |
| `backends/<service>/` | Service-specific parameters, endpoints and optional resource management |
| `recording/` | Client/context/provider/training views and archived image references |
| `image_encoding.py` | Shared image encoding used by service backends |

Add an adapter under its service directory and a `ProviderProfile` in the
registry. Implement `target_url`, `encode_request`, and `decode_response`, with
explicit per-request decode state and training views. Declare the input and
upstream protocols; only register conversion pairs that are implemented. Use an
optional provider session for resources such as DeepSeek Files uploads and leases.
Its `prepare_request`, `finish_request`, and `close` hooks have distinct lifetimes.

Use `list_provider_profiles`, `validate_provider_profile`, and
`build_provider_adapter(name, client_protocol=...)` for all callers. Adding a
Messages backend does not require a Claude-specific branch, and adding a
Responses backend does not require changes to Codex or the shared gripper tools.
Planner SDK bindings remain under `agents/`; providers do not execute robot tools.

Context selection precedes conversion. The client checks raw upstream errors
using the upstream protocol before conversion. The gateway delivers the resulting
client protocol and commits SDK bindings only after successful delivery. Keep
this ordering, cancellation, image evidence, tool identity, and retry behavior
covered by tests. Messages heartbeat frames do not authorize tool execution.

## Python interface

Import public clients and factories from `uni_robot_agent.provider` and interface
types from `provider.contracts`. Implementations live under `backends/`,
conversions under `conversions/`, and exchange recording under `recording/`.
The DeepSeek broker entry point is
`python -m uni_robot_agent.provider.backends.deepseek.file_broker`.
The gateway binding argument is `model_binding`; use the `ModelBinding` contract.

Provider profiles select protocol conversion and backend behavior. The context
policy named `identity` controls conversation selection independently.

Adapters may implement `RequestValidatingAdapter.validate_request` to validate
the original wire history before context selection. Gemini's Qwen adapter uses
this boundary so unsupported content cannot disappear through history pruning.
Validation traverses native parts without encoding or copying image payloads;
provider encoding still operates on the selected effective request.
