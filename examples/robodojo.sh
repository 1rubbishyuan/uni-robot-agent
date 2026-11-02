#!/usr/bin/env bash
# Minimal Direct episode using the CLI-managed RoboDojo runtime.
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
export PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}"
mode="${1:---help}"
if (( $# )); then shift; fi

case "$mode" in
  setup)
    exec "${PYTHON:-python}" -m uni_robot_agent setup --integration robodojo "$@"
    ;;
  run)
    exec "${PYTHON:-python}" -m uni_robot_agent run \
      --integration robodojo --deploy local --task stack_bowls \
      --action-profile direct --no-sam3 --output-dir ./runs/robodojo "$@"
    ;;
  -h|--help)
    cat <<'HELP'
Usage:
  bash examples/robodojo.sh setup --wheel /path/to/uni_robot_agent.whl
  bash examples/robodojo.sh run --model YOUR_MODEL [CLI options]

Prepare once on a supported Linux GPU host with Python 3.11 available.
Set provider credentials in your shell; see examples/README.md.
Run starts one stack_bowls episode and cleans up its owned environment.
Options after the mode are forwarded unchanged and override example defaults:
  --task TASK              Select another RoboDojo task
  --env-gpu 0              Select the simulator GPU
  --no-depth               Use RGB with calibration
  --monitor                Open a local live-monitor endpoint
  --validate-config        Check arguments without starting a simulator/model
  --help                   Show all CLI options for setup or run
HELP
    ;;
  *) printf 'Unknown mode: %s. Use setup, run, or --help.\n' "$mode" >&2; exit 2 ;;
esac
