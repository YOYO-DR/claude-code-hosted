#!/usr/bin/env bash
# Corre como root (ExecStartPre con prefijo '+'). Asigna un puerto fijo al
# slug (reutilizandolo si ya existia) y regenera el router dinamico de
# Traefik para todos los slugs activos. Ver DECISIONS.md D4.
set -euo pipefail

SLUG="$1"
BASE_DIR="/opt/panel/deploy/ttyd"
PORTS_FILE="${BASE_DIR}/ports.json"
LOCK_FILE="${BASE_DIR}/ports.lock"
RANGE_START=7681
RANGE_END=7688

[[ -f "$PORTS_FILE" ]] || echo '{}' > "$PORTS_FILE"

exec 9>"$LOCK_FILE"
flock 9

PORT=$(jq -r --arg s "$SLUG" '.[$s] // empty' "$PORTS_FILE")
if [[ -z "$PORT" ]]; then
  for p in $(seq "$RANGE_START" "$RANGE_END"); do
    if ! jq -e --argjson p "$p" '[.[]] | index($p)' "$PORTS_FILE" >/dev/null; then
      PORT=$p
      break
    fi
  done
  if [[ -z "$PORT" ]]; then
    echo "No hay puertos ttyd libres (${RANGE_START}-${RANGE_END})" >&2
    exit 1
  fi
  jq --arg s "$SLUG" --argjson p "$PORT" '.[$s] = $p' "$PORTS_FILE" > "${PORTS_FILE}.tmp"
  mv "${PORTS_FILE}.tmp" "$PORTS_FILE"
  chmod 644 "$PORTS_FILE"
fi

/opt/panel/deploy/ttyd/render_routes.py
