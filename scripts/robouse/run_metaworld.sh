#!/usr/bin/env bash
set -euo pipefail
robouse_backend=metaworld
robouse_cameras=(corner topview)
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh" "$@"
