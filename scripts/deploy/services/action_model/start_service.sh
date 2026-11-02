#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../../.." && pwd -P)"
export PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}"
: "${ACTION_MODEL_PYTHON:?Set the interpreter for the selected action_model runtime}"
exec "$ACTION_MODEL_PYTHON" -m uni_robot_agent.services.actor.server "$@"
