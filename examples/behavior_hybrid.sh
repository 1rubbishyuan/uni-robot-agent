#!/usr/bin/env bash
set -euo pipefail

: "${BEHAVIOR_ENV_URL:?Set the prepared environment service URL}"
: "${ACTOR_URL:?Set the OpenPI actor service URL}"
: "${ACTOR_CHECKPOINT_SHA256:?Set the verified checkpoint identity}"
: "${PROVIDER_URL:?Set the planner model provider URL}"
: "${MODEL:?Set the planner model name}"

uni-robot-agent run \
  --integration behavior \
  --tool-profile hybrid \
  --env-url "$BEHAVIOR_ENV_URL" \
  --action-model-url "$ACTOR_URL" \
  --action-model-checkpoint-sha256 "$ACTOR_CHECKPOINT_SHA256" \
  --action-model-binding behavior_r1pro_joint \
  --provider-url "$PROVIDER_URL" --model "$MODEL" \
  --no-sam3 --no-vla-review "$@"
