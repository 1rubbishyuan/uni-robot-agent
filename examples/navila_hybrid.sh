#!/usr/bin/env bash
set -euo pipefail
MODEL=${MODEL:-qwen3.8-flash}
# Prepare and start the independent environment and actor as described in docs/navila.md.
# Choose isaac41 or explicit isaac51 when starting the environment behind ENV_URL.
: "${ENV_URL:?Set the prepared NaVILA environment URL}"
: "${ACTOR_URL:?Set the harness NaVILA bridge URL}"
: "${CHECKPOINT_SHA256:?Set the declared upstream model digest}"

uni-robot-agent run --integration navila --tool-profile hybrid \
  --env-url "$ENV_URL" --action-model-url "$ACTOR_URL" \
  --action-model-checkpoint-sha256 "$CHECKPOINT_SHA256" \
  --model "$MODEL" --no-vla-review --no-sam3 "$@"
