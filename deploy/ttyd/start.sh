#!/usr/bin/env bash
# Corre como usuario 'agents' (ExecStart de ttyd@.service). El puerto ya fue
# asignado por allocate.sh (ExecStartPre, corre como root) y la sesion tmux
# cc-<slug> la garantiza tmux@<slug>.service (Requires). Aqui solo se adjunta:
# si ttyd muere, la sesion sigue viva en su propia unidad.
set -euo pipefail

SLUG="$1"
PORTS_FILE="/opt/panel/deploy/ttyd/ports.json"

PORT=$(jq -r --arg s "$SLUG" '.[$s] // empty' "$PORTS_FILE")
if [[ -z "$PORT" ]]; then
  echo "Slug '${SLUG}' sin puerto asignado en ${PORTS_FILE}" >&2
  exit 1
fi

exec /usr/bin/ttyd \
  --interface 127.0.0.1 \
  --port "$PORT" \
  --base-path "/projects/${SLUG}/terminal" \
  --writable \
  tmux new -A -s "cc-${SLUG}"
