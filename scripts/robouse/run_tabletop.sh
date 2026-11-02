#!/usr/bin/env bash
set -euo pipefail
robouse_backend=tabletop
robouse_cameras=(front top)
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh" "$@"
