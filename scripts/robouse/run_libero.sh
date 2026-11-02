#!/usr/bin/env bash
set -euo pipefail
robouse_backend=libero
robouse_cameras=(agentview robot0_eye_in_hand)
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh" "$@"
