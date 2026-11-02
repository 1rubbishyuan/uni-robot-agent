#!/usr/bin/env bash
set -euo pipefail
robouse_backend=robosuite
robouse_cameras=(agentview)
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh" "$@"
