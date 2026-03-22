#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="$ROOT_DIR/.venv/bin/python"
SERVICE_SCRIPT="$ROOT_DIR/scripts/metagpt-codex-service.py"

if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "[metagpt-codex-service] Missing Python executable: $VENV_PYTHON" >&2
  echo "Ask the assistant to (re)build the local .venv first." >&2
  exit 1
fi

exec "$VENV_PYTHON" "$SERVICE_SCRIPT" "$@"
