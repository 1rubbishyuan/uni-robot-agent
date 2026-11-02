#!/usr/bin/env bash
set -euo pipefail
robouse_backend=menagerie
robouse_cameras=(workspace top)
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh" "$@"
