# Antigravity backend

The experimental `antigravity` backend runs Google's Antigravity **Python SDK**
and its bundled native harness. It does not automate the Antigravity desktop
application, reuse Gemini CLI, or use a Google subscription as model credentials.
The initial supported path uses Qwen through the existing provider gateway.

## Install and run

```bash
python -m pip install 'uni-robot-agent[antigravity]'
export UNI_ROBOT_AGENT_API_KEY='<provider-key>'
export UNI_ROBOT_AGENT_PROVIDER_URL='https://dashscope.aliyuncs.com/compatible-mode/v1'
# The SDK's local WebSocket must bypass any configured HTTP/SOCKS proxy.
export NO_PROXY="${NO_PROXY:+$NO_PROXY,}127.0.0.1,localhost"
export no_proxy="$NO_PROXY"

uni-robot-agent run --integration robodojo \
  --agent antigravity --model qwen3.8-flash \
  --provider-profile aliyun.gemini_chat_completions \
  --reasoning-effort none --context-policy identity \
  --env-url http://environment-host:19260 --sam3-url http://sam-host:18124 \
  --interaction-mode tool_calling --action-profile direct \
  --max-turns 0 --episode-timeout-s 10800
```

Prepare the environment and perception services separately; their interpreters
do not need the Antigravity extra. Configure depth and camera calibration when
starting the environment service. See [running](running.md) and [services](services.md).
The model must support image input and function calling. Set credentials through
the usual provider configuration, never through the local SDK endpoint.

The optional dependency is pinned to `google-antigravity==0.1.23`. The adapter
checks the installed version and records the bundled native binary's SHA256.
No separately patched CLI or bridge manifest is needed. Base-package imports,
CLI help and `--validate-config` work without the optional SDK installed.

## Supported behavior

- `tool_calling`, an explicit provider URL, `aliyun.gemini_chat_completions`,
  non-thinking Qwen, and identity harness context.
- The assembled robot system instructions replace the SDK's default prompt.
  The SDK still wraps user messages and adds its native metadata.
- Robot, observation, job and runtime-skill tools use the existing authenticated
  HTTP MCP server and executor. MCP tools are loaded eagerly.
- Native file and command tools support calculations in the session scratch
  directory. The SDK workspace includes the session root so tool-supplied
  images and calibration files are readable. The storage-access prompt defines
  the allowed read/write scopes; this is not an OS sandbox. Commands use the
  harness interpreter directory on PATH, with a 60-second command timeout and
  native daemons disabled. The SDK waits for background commands to settle;
  its `manage_task` tool can inspect or cancel them. Web search, delegation, image generation and
  Code-as-Policy are not enabled in this first stage. Runtime skills remain
  accessible through the harness `list_skills` and `read_skill` tools.
- `--max-turns` counts delivered model exchanges; zero disables that limit.
  The episode timeout, provider failures and user cancellation stop native
  generation. SDK model retries are configured to zero; gateway failure events
  also cancel the SDK so its outer loop cannot repeat a failed exchange.
- Identity applies to the harness context policy. The SDK retains its own
  default compaction behavior; this does not promise unlimited native history.

Google-native models, thinking mode and other provider profiles require separate
validation. `--agent-python` is a Codex option and is not used here.

## Call identity and protocol boundary

The gateway buffers a complete model response before exposing its calls. The
SDK pre-tool hook attaches the exact native call ID to a private transport
argument. The MCP handler removes that field before JSON-schema validation and
checks the ID, tool name, original arguments, delivery status and batch order.
Duplicate or mismatched callbacks never replay a robot command. Hooks authorize
and carry identity; only the executor runs tools.

The SDK adds `toolSummary` and `toolAction` fields to model declarations and
removes them before invoking MCP hooks. The Antigravity binding removes only
those SDK display labels from its comparison view; original wire records retain
them. Harness tools cannot declare conflicting argument names.

A dedicated `provider/protocols/antigravity.py` normalizes the pinned runtime's
GenerateContent dialect before the existing Qwen conversion:

- Drop `sessionId`, which is native routing metadata unsupported by Qwen.
- Convert contents consisting of function responses from `model` to `user`
  role, preserving call IDs, errors and image attachments.
- Map the SDK's exact `functionCallingConfig: {mode: NONE}` to `AUTO` when it
  supplies tool declarations. Version 0.1.23 emits this configuration even with
  tools explicitly enabled. This adapter intentionally enables automatic tool
  selection for its supported `tool_calling` mode.

These changes apply only to Antigravity. Gemini CLI, Codex, shared tool schemas,
execution scheduling and environment integrations retain their behavior. The
Qwen provider boundary converts PNG inputs to lossless WebP, including images
attached to failed tool results.

## Validation and records

From a source checkout, exercise an installed candidate with inert tools before
connecting a robot:

```bash
python scripts/validation/agent_backends.py \
  --agent antigravity --model qwen3.8-flash --output /path/to/new-smoke-run
```

The smoke checks an image-derived color, an observation nonce, two identical
calls with distinct IDs, finish acknowledgement and complete provider records.
It makes real Qwen requests but performs no robot actions.

Each episode saves `antigravity/runtime.json`, native SDK state,
`antigravity/tool-calls.jsonl`, `antigravity/native-events.jsonl`, completion
usage and gateway exchanges in `antigravity_generate_content/`. The binary hash,
model, tool capabilities and context choices are recorded with the run.
Validated SFT export is not implemented for this backend.

A submitted harness `finish`, normal SDK completion and error-free provider
records establish agent completion. The environment's `task_success` remains
separate; a native SDK finish message alone is never evidence of robot success.
