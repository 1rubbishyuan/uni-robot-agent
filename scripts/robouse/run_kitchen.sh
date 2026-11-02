#!/usr/bin/env bash
set -euo pipefail
robouse_backend=kitchen
robouse_cameras=(front)
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh" "$@"
