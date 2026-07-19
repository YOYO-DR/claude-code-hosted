#!/usr/bin/env bash
# Caos: reboot frío. Antes de rebootar deja constancia del estado; tras el reboot
# (correr con --verify) comprueba que infra+panel están arriba y que las sesiones
# sin heartbeat quedaron `crashed` (estado honesto), no "running" fantasma.
#
# Uso:
#   cold_reboot.sh            # registra estado y reinicia el VPS
#   cold_reboot.sh --verify   # tras el arranque: verifica recuperación
set -euo pipefail

STATE=/var/tmp/chaos_reboot_state.txt

if [[ "${1:-}" == "--verify" ]]; then
  echo "== infra + panel =="
  systemctl is-active panel-infra.service panel.service tg-bridge.service || true
  echo "== corriendo monitor (marca crashed las sesiones sin heartbeat) =="
  systemctl start monitor.service || true
  sleep 3
  cd /opt/panel; set -a; . /etc/panel/panel.env; set +a; export DJANGO_SETTINGS_MODULE=panel.settings
  /opt/panel/.venv/bin/python -c "
import sys; sys.path.insert(0,'/opt/panel')
import django; django.setup()
from panel.core.models import Session
fantasmas = Session.objects.filter(status__in=['running','idle','waiting_approval']).count()
print('sesiones RUNNING/IDLE/WAITING tras reboot (deben ser 0):', fantasmas)
print('OK' if fantasmas == 0 else 'REVISAR: hay sesiones fantasma')
"
  exit 0
fi

echo "== estado pre-reboot =="
systemctl list-units 'claude-session@*' --no-legend --plain 2>/dev/null | awk '{print $1}' | tee "$STATE"
echo "== rebooting en 3s (corre 'cold_reboot.sh --verify' al volver) =="
sleep 3
systemctl reboot
