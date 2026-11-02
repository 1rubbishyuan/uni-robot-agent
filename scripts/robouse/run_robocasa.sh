#!/usr/bin/env bash
set -euo pipefail
robouse_backend=robocasa
robouse_sim=
robouse_cameras=(robot0_agentview_center)
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh" "$@"
