#!/usr/bin/env bash
# Connect the planner to a prepared pick_bucket environment and identified actor.
set -euo pipefail
: "${ENV_URL:?Set the prepared DexJoCo environment URL}"
: "${ACTOR_URL:?Set the OpenPI actor URL}"
: "${ACTOR_CHECKPOINT_SHA256:?Set the verified actor checkpoint SHA256}"

# Start the independent services first; see docs/dexjoco.md for setup and ports.
# Extra arguments select the planner model, provider and reviewer configuration.
exec uni-robot-agent run \
  --integration dexjoco \
  --task pick_bucket \
  --env-url "$ENV_URL" \
  --tool-profile hybrid \
  --action-model-binding dexjoco_tcp_hand \
  --action-model-url "$ACTOR_URL" \
  --action-model-checkpoint-sha256 "$ACTOR_CHECKPOINT_SHA256" \
  --no-sam3 \
  --max-turns 0 \
  --episode-timeout-s 10800 \
  "$@"
