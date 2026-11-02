#!/usr/bin/env bash
set -euo pipefail
repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
export PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}"
: "${HARNESS_PYTHON:?Set HARNESS_PYTHON to the harness interpreter}"
: "${ENV_URL:?Set ENV_URL to the prepared RoboUse environment service}"
# Supply --sam3-url or --no-sam3 explicitly; budgets can be overridden.
exec "$HARNESS_PYTHON" -m uni_robot_agent run --integration robouse --env-url "$ENV_URL" \
  --action-profile direct --episode-timeout-s 10800 --max-turns 0 "$@"
