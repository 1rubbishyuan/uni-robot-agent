#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../../.." && pwd -P)"
export PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}"
: "${SIM_PYTHON:?Set the Isaac interpreter}"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export VK_ICD_FILENAMES="${VK_ICD_FILENAMES:-$script_dir/nvidia_icd.json}"
exec "$SIM_PYTHON" -m uni_robot_agent.integrations.robodojo.environment.server "$@"
