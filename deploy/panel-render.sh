#!/usr/bin/env bash
# Re-materializa todos los proyectos (§4.3) como root: root lee /etc/panel/panel.env
# (DB creds) y puede escribir config en dirs de `agents`. Lo invoca el panel vía
# `sudo -n` (sudoers restringido). Idempotente.
set -euo pipefail
set -a
. /etc/panel/panel.env
set +a
export DJANGO_SETTINGS_MODULE=panel.settings
cd /opt/panel
exec /opt/panel/.venv/bin/python scripts/render_all.py
