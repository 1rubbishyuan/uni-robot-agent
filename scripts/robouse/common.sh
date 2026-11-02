#!/usr/bin/env bash
# Called by an environment profile; choices remain visible in its script.
set -euo pipefail
repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
export PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}"
: "${ROBOUSE_PYTHON:?Set ROBOUSE_PYTHON to the isolated environment interpreter}"
worker_args=()
if [[ -n "${robouse_sim:-}" ]]; then worker_args+=(--sim "$robouse_sim"); fi
if [[ -n "${ROBOUSE_WORKER_PYTHON:-}" ]]; then worker_args+=(--worker-python "$ROBOUSE_WORKER_PYTHON"); fi
exec "$ROBOUSE_PYTHON" -m uni_robot_agent.integrations.robouse.environment.server \
  --backend "$robouse_backend" --cameras "${robouse_cameras[@]}" --no-depth "${worker_args[@]}" "$@"
