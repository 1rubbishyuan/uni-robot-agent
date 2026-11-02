#!/usr/bin/env bash
set -euo pipefail
robouse_backend=components
robouse_cameras=(overview)
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh" "$@"
