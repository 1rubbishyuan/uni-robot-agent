#!/usr/bin/env bash
# Render the approved vector PDF at 4x its 1920 x 1544 CSS-pixel artboard.
set -euo pipefail
asset_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if ! command -v pdftoppm >/dev/null 2>&1; then
  echo "Poppler is required (pdftoppm). Install it, then rerun this script." >&2
  exit 1
fi
export_dir="$(mktemp -d)"
trap 'rm -rf "$export_dir"' EXIT
pdftoppm -f 1 -singlefile -r 384 -png \
  "$asset_dir/uni-robot-agent-architecture.pdf" "$export_dir/cover"
mv "$export_dir/cover.png" "$asset_dir/uni-robot-agent-architecture.png"
echo "Exported uni-robot-agent-architecture.png (7680 x 6176)."
