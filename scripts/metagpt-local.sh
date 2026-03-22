#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_METAGPT="$ROOT_DIR/.venv/bin/metagpt"
VENV_PYTHON="$ROOT_DIR/.venv/bin/python"

if [[ ! -x "$VENV_METAGPT" ]]; then
  echo "[metagpt-local] Missing executable: $VENV_METAGPT" >&2
  echo "Ask the assistant to (re)build the local .venv first." >&2
  exit 1
fi

if [[ "${1-}" == "python" ]]; then
  shift
  exec "$VENV_PYTHON" "$@"
fi

exec "$VENV_METAGPT" "$@"
