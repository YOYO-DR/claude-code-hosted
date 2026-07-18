#!/usr/bin/env bash
# Corre como root, despues de que /opt/panel ya sea el checkout del repo y de
# haber corrido install.sh. Simlinkea las unidades, aplica migraciones y
# levanta el panel. Idempotente: git pull + este script recoge cambios.
set -euo pipefail

SRC="/opt/panel/deploy/systemd"
DST="/etc/systemd/system"
VENV="/opt/panel/.venv/bin"

for unit in panel-infra.service panel.service "tmux@.service" "ttyd@.service" "claude-session@.service"; do
  ln -sf "${SRC}/${unit}" "${DST}/${unit}"
done

systemctl daemon-reload
systemctl enable --now panel-infra.service

echo "==> Esperando a Postgres..."
for _ in $(seq 1 30); do
  if runuser -u panel -- bash -c 'set -a; source /etc/panel/panel.env; set +a;
       '"$VENV"'/python -c "import psycopg,os; psycopg.connect(host=os.environ[\"PANEL_DB_HOST\"], port=os.environ[\"PANEL_DB_PORT\"], dbname=os.environ[\"PANEL_DB_NAME\"], user=os.environ[\"PANEL_DB_USER\"], password=os.environ[\"PANEL_DB_PASSWORD\"]).close()"' 2>/dev/null; then
    echo "  Postgres listo."
    break
  fi
  sleep 2
done

echo "==> Migraciones + estáticos"
runuser -u panel -- bash -c "set -a; source /etc/panel/panel.env; set +a;
  cd /opt/panel && ${VENV}/python manage.py migrate --noinput &&
  ${VENV}/python manage.py collectstatic --noinput"

echo "==> Levantando el panel"
systemctl enable --now panel.service
systemctl restart panel.service
