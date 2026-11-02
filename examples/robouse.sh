#!/usr/bin/env bash
# Minimal Direct LIBERO episode using the CLI-managed RoboUse runtime.
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
export PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}"
mode="${1:---help}"
if (( $# )); then shift; fi

case "$mode" in
  setup)
    exec "${PYTHON:-python}" -m uni_robot_agent setup \
      --integration robouse --runtime-profile libero "$@"
    ;;
  run)
    exec "${PYTHON:-python}" -m uni_robot_agent run \
      --integration robouse --deploy local --runtime-profile libero \
      --task libero-10-0 --action-profile direct --no-sam3 \
      --output-dir ./runs/robouse "$@"
    ;;
  -h|--help)
    cat <<'HELP'
Usage:
  bash examples/robouse.sh setup --wheel /path/to/uni_robot_agent.whl
  bash examples/robouse.sh run --model YOUR_MODEL [CLI options]

Prepare once on a supported Linux GPU host with Python 3.11 available.
Set provider credentials in your shell; see examples/README.md.
Run starts the bundled libero-10-0 task and cleans up its owned environment.
Options after the mode are forwarded unchanged and override example defaults:
  --task TASK              Select another bundled task in the prepared profile
  --runtime-profile NAME   Select the same profile for setup and run
  --env-gpu 0              Select the simulator GPU
  --monitor                Open a local live-monitor endpoint
  --validate-config        Check arguments without starting a simulator/model
  --help                   Show all CLI options for setup or run
HELP
    ;;
  *) printf 'Unknown mode: %s. Use setup, run, or --help.\n' "$mode" >&2; exit 2 ;;
esac
