#!/usr/bin/env bash
# Caos: Postgres caído N s. El worker debe fallar limpio (sin corrupción) y al
# volver PG debe operar normalmente. Uso: pg_outage.sh [segundos]
set -euo pipefail
SECS="${1:-20}"
C=panel-infra-postgres-1

echo "== docker stop postgres ($SECS s) =="
docker stop "$C" >/dev/null
sleep "$SECS"
echo "== docker start postgres =="
docker start "$C" >/dev/null
sleep 8
echo "== integridad: la DB responde y el conteo de eventos es coherente =="
docker exec "$C" psql -U panel -d panel -tAc "select 'events='||count(*) from core_event"
docker exec "$C" psql -U panel -d panel -tAc "select 'sessions='||count(*) from core_session"
echo "== panel sigue vivo =="
systemctl is-active panel.service
echo "OK"
