#!/usr/bin/env bash
# Creates an isolated venv; never mutates running simulator environments.
set -euo pipefail
stack=${1:?Usage: setup.sh mujoco|libero NEW_VENV_PATH [BASE_PYTHON]}
venv_dir=${2:?Supply a new persistent environment path}
base_python=${3:-python3}
case "$stack" in mujoco|libero) ;; *) echo "Unknown stack: $stack" >&2; exit 2;; esac
if [[ -e "$venv_dir" ]]; then echo "Refusing to overwrite existing environment: $venv_dir" >&2; exit 2; fi
"$base_python" -m venv "$venv_dir"
"$venv_dir/bin/python" -m pip install -r "$(dirname -- "${BASH_SOURCE[0]}")/requirements-$stack.txt"
"$venv_dir/bin/python" -m pip install "$(dirname -- "${BASH_SOURCE[0]}")/../.."
"$venv_dir/bin/python" -m pip freeze > "$venv_dir/installed.txt"
