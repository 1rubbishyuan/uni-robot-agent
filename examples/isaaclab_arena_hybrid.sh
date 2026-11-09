#!/usr/bin/env bash
set -euo pipefail

# First prepare the two independent runtimes; see docs/isaaclab_arena.md.
# Terminal 1:
# uni-robot-agent deploy --service actor --profile arena_g1_gr00t --port 18762
# Terminal 2:
# uni-robot-agent deploy --service env --integration isaaclab_arena \
#   --task galileo_g1_locomanip_pick_and_place --seed 0 --port 18789
# Terminal 3 runs this script with normal planner/provider options appended.

: "${ACTOR_CHECKPOINT_SHA256:?Set the verified digest from actor setup}"
uni-robot-agent run \
  --integration isaaclab_arena --tool-profile hybrid \
  --env-url "${ARENA_ENV_URL:-http://127.0.0.1:18789}" \
  --action-model-url "${ACTOR_URL:-http://127.0.0.1:18762}" \
  --action-model-binding arena_g1_locomanipulation \
  --action-model-checkpoint-sha256 "$ACTOR_CHECKPOINT_SHA256" \
  --no-vla-review --no-sam3 "$@"
