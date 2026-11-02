#!/usr/bin/env bash
# Connect to a prepared RoboDojo episode and a matching Pi0.5 actor service.
# Deploy services separately using docs/deployment.md; this script does not own them.
# Configure provider credentials in your shell, as in examples/README.md.
# Pass the actual checkpoint SHA-256 advertised by the actor service:
#   bash examples/robodojo_hybrid.sh --model "$MODEL" \
#     --action-model-checkpoint-sha256 "$CHECKPOINT_SHA256"
# Append --validate-config for an offline check, or --help for all CLI options.
# This minimal example uses no SAM3 and keeps normal budgets and task supplements.
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
export PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}"

exec "${PYTHON:-python}" -m uni_robot_agent run \
  --integration robodojo --task stack_bowls \
  --env-url "${UNI_ROBOT_AGENT_ENV_URL:-http://127.0.0.1:18760}" \
  --action-model-url "${UNI_ROBOT_AGENT_ACTION_MODEL_URL:-http://127.0.0.1:18761}" \
  --action-profile hybrid --no-sam3 --output-dir ./runs/robodojo-hybrid "$@"
