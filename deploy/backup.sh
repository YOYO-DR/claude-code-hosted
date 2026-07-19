#!/usr/bin/env bash
# Backup diario (§6.1): pg_dump (formato custom) + ~/.claude de agents + los
# .claude de cada proyecto → un tar cifrado (AES-256, openssl) local. Retención
# de 7. Corre como root desde backup.service.
set -euo pipefail

BACKUP_DIR=/var/backups/panel
KEYFILE=/etc/panel/backup.pass
PG_CONTAINER=panel-infra-postgres-1
RETENTION=7

mkdir -p "$BACKUP_DIR"
if [[ ! -f "$KEYFILE" ]]; then
  ( umask 077; openssl rand -base64 48 > "$KEYFILE" )
  chown root:root "$KEYFILE"; chmod 600 "$KEYFILE"
fi

# shellcheck disable=SC1091
set -a; . /etc/panel/panel.env; set +a

ts=$(date +%Y%m%d-%H%M%S)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

echo "==> pg_dump"
docker exec "$PG_CONTAINER" pg_dump -U "$PANEL_DB_USER" -Fc "$PANEL_DB_NAME" > "$work/panel.dump"

echo "==> configs .claude"
paths=()
[[ -d /home/agents/.claude ]] && paths+=(/home/agents/.claude)
while IFS= read -r d; do paths+=("$d"); done \
  < <(find /srv/projects -maxdepth 2 -name .claude -type d 2>/dev/null)
if [[ ${#paths[@]} -gt 0 ]]; then
  tar czf "$work/claude-configs.tar.gz" --absolute-names "${paths[@]}" 2>/dev/null || true
else
  : > "$work/claude-configs.tar.gz"
fi

echo "==> tar + cifrado"
out="$BACKUP_DIR/panel-$ts.tar.enc"
tar cf - -C "$work" panel.dump claude-configs.tar.gz \
  | openssl enc -aes-256-cbc -pbkdf2 -salt -pass file:"$KEYFILE" -out "$out"
chmod 600 "$out"
echo "backup: $out ($(du -h "$out" | cut -f1))"

echo "==> retención local (últimos $RETENTION)"
ls -1t "$BACKUP_DIR"/panel-*.tar.enc 2>/dev/null | tail -n +$((RETENTION + 1)) | xargs -r rm -f

echo "==> subida a S3 (si está configurado)"
/opt/panel/.venv/bin/python /opt/panel/scripts/s3_backup.py upload "$out" || echo "WARN: subida S3 falló (backup local intacto)"
echo "OK"
