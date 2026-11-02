#!/usr/bin/env bash
# Service configuration is explicit CLI; PYTHON selects its interpreter.
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../../.." && pwd -P)"
export PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}"
exec "${PYTHON:-python}" -m uni_robot_agent.services.perception.server "$@"
