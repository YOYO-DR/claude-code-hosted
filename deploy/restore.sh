#!/usr/bin/env bash
# Restore de un backup (§6.1). Uso:
#   restore.sh <panel-YYYYmmdd-HHMMSS.tar.enc> [db_destino]
# Por defecto restaura a la DB `panel_restore_test` (NO pisa producción) para
# poder probar el restore en limpio. Corre como root.
set -euo pipefail

enc="${1:?uso: restore.sh <archivo.tar.enc> [db_destino]}"
target_db="${2:-panel_restore_test}"
KEYFILE=/etc/panel/backup.pass
PG_CONTAINER=panel-infra-postgres-1

# shellcheck disable=SC1091
set -a; . /etc/panel/panel.env; set +a

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

echo "==> descifrando + extrayendo"
openssl enc -d -aes-256-cbc -pbkdf2 -pass file:"$KEYFILE" -in "$enc" | tar xf - -C "$work"
ls -l "$work"

echo "==> recreando DB $target_db y restaurando"
docker exec "$PG_CONTAINER" psql -U "$PANEL_DB_USER" -d "$PANEL_DB_NAME" \
  -c "DROP DATABASE IF EXISTS $target_db" >/dev/null
docker exec "$PG_CONTAINER" psql -U "$PANEL_DB_USER" -d "$PANEL_DB_NAME" \
  -c "CREATE DATABASE $target_db" >/dev/null
docker exec -i "$PG_CONTAINER" pg_restore -U "$PANEL_DB_USER" -d "$target_db" < "$work/panel.dump"

echo "==> configs .claude disponibles en: $work/claude-configs.tar.gz"
echo "OK — restaurado en DB '$target_db'"
