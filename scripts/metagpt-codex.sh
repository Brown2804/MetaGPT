#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE="$ROOT_DIR/scripts/metagpt-codex-service.sh"
METAGPT="$ROOT_DIR/scripts/metagpt-local.sh"

case "${1-}" in
  setup|ensure|start|stop|status|logs|smoke)
    exec "$SERVICE" "$@"
    ;;
esac

"$SERVICE" ensure >/dev/null
exec "$METAGPT" "$@"
