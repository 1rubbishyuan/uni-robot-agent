#!/usr/bin/env bash
# Connect to a prepared RoboDojo episode; forward runtime options to the CLI.
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd -P)"
export PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}"
exec "${PYTHON:-python}" -m uni_robot_agent.cli run --integration robodojo "$@"
