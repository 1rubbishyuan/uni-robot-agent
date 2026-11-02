#!/usr/bin/env bash
set -euo pipefail
robouse_backend=gpu
robouse_sim=isaaclab
robouse_cameras=(scene close)
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh" "$@"
