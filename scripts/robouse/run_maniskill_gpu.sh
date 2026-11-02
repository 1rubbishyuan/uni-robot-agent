#!/usr/bin/env bash
set -euo pipefail
robouse_backend=gpu
robouse_sim=maniskill
robouse_cameras=(scene front)
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh" "$@"
