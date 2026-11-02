#!/usr/bin/env bash
set -euo pipefail
robouse_backend=gymrobotics
robouse_cameras=(default)
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh" "$@"
