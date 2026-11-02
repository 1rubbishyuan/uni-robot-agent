#!/usr/bin/env bash
# Run the prepared RoboLab simulator and the planner in separate terminals.
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
export PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}"
mode="${1:---help}"
if (( $# )); then shift; fi

case "$mode" in
  serve)
    : "${SIM_PYTHON:?Set SIM_PYTHON to the prepared RoboLab/Isaac Python interpreter; see docs/robolab.md}"
    exec "$SIM_PYTHON" -m uni_robot_agent.integrations.robolab.environment.server \
      --task BananaInBowlTask --seed 0 --instruction-type default \
      --headless --device cuda:0 --host 127.0.0.1 --port 18770 "$@"
    ;;
  run)
    exec "${PYTHON:-python}" -m uni_robot_agent run \
      --integration robolab --env-url http://127.0.0.1:18770 \
      --task BananaInBowlTask --seed 0 --instruction-type default \
      --action-profile direct --no-sam3 --output-dir ./runs/robolab "$@"
    ;;
  -h|--help)
    cat <<'HELP'
Usage (two terminals):
  SIM_PYTHON=/path/to/robolab/python bash examples/robolab.sh serve \
    --output-dir /path/to/new/environment-directory
  bash examples/robolab.sh run --model YOUR_MODEL [CLI options]

Prepare the pinned RoboLab/Isaac runtime and assets using docs/robolab.md.
Set provider credentials in the planner terminal; see examples/README.md.
Wait until the service is ready before running the planner. Restart serve with
a new output directory for each episode; stop the service with Ctrl-C afterward.
Options after the mode are forwarded unchanged and override example defaults:
  --task TASK --seed N     Use matching values in both terminals
  --device cuda:0         Simulator GPU (serve)
  --port 18770            Service port (serve); match --env-url when running
  --env-url URL           Connect to your prepared service (run)
  --monitor               Open a local live-monitor endpoint (run)
  --validate-config       Check planner arguments without connecting (run)
HELP
    ;;
  *) printf 'Unknown mode: %s. Use serve, run, or --help.\n' "$mode" >&2; exit 2 ;;
esac
