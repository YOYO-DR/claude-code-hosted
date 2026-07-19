#!/usr/bin/env bash
# Caos: llenar / por encima del 90% para verificar que el monitor alerta. Libera
# el espacio al terminar. Uso: disk_fill.sh
set -euo pipefail
FILLER=/var/tmp/chaos_fill.bin
trap 'rm -f "$FILLER"; echo "== liberado =="' EXIT

avail_kb=$(df --output=avail / | tail -1)
total_kb=$(df --output=size / | tail -1)
# objetivo: dejar ~5% libre → llenar (avail - 5% del total)
target_free_kb=$(( total_kb / 20 ))
fill_kb=$(( avail_kb - target_free_kb ))
echo "== fallocate ${fill_kb}K en $FILLER (para >90%) =="
fallocate -l "${fill_kb}K" "$FILLER"
df -h / | tail -1

echo "== corriendo monitor (debe alertar disco) =="
cd /opt/panel; set -a; . /etc/panel/panel.env; set +a; export DJANGO_SETTINGS_MODULE=panel.settings
/opt/panel/.venv/bin/python -c "
import sys; sys.path.insert(0,'/opt/panel')
import django; django.setup()
from panel.core.models import Config
Config.objects.filter(key='alert:disk').delete()
from scripts import monitor
monitor.check_disk()
print('monitor ejecutado')
"
echo "OK (el archivo de relleno se libera al salir)"
