#!/usr/bin/env bash
# Caos: Redis caído N s. El worker debe reintentar (no morir) y recuperar; los
# eventos ya persistidos en PG no se pierden. Uso: redis_outage.sh [segundos]
set -euo pipefail
SECS="${1:-30}"
C=panel-infra-redis-1

echo "== eventos en PG antes =="
BEFORE=$(docker exec panel-infra-postgres-1 psql -U panel -d panel -tAc "select count(*) from core_event")
echo "$BEFORE"
echo "== docker stop redis ($SECS s) =="
docker stop "$C" >/dev/null
sleep "$SECS"
echo "== docker start redis =="
docker start "$C" >/dev/null
sleep 8
echo "== workers vivos tras el corte =="
systemctl list-units 'claude-session@*' --no-legend --plain 2>/dev/null | awk '{print $1, $4}'
AFTER=$(docker exec panel-infra-postgres-1 psql -U panel -d panel -tAc "select count(*) from core_event")
echo "== eventos en PG después: $AFTER (antes $BEFORE) — no deben disminuir =="
[ "$AFTER" -ge "$BEFORE" ] && echo "OK: sin pérdida de eventos" || echo "FALLO: eventos perdidos"
