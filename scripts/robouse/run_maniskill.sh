#!/usr/bin/env bash
set -euo pipefail
robouse_backend=maniskill
robouse_sim=
robouse_cameras=(render_camera base_camera)
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh" "$@"
