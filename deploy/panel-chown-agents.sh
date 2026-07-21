#!/usr/bin/env bash
# SP8: chown agents:agents de archivos que el render escribe bajo
# /srv/projects/. Lo invoca el panel vía `sudo -n` desde
# `panel.core.services.privileged.chown_agents`. Idempotente: si el path
# no existe (race con prune / primer render), devuelve 0 sin error — el
# render solo necesita el chown de los archivos que sí escribió.
set -euo pipefail

path="${1:?uso: panel-chown-agents.sh <path-absoluto-bajo-/srv/projects>}"

case "$path" in
  /srv/projects/*) : ;;
  *) echo "path fuera de /srv/projects: $path" >&2; exit 2 ;;
esac
if [ -e "$path" ]; then
  chown agents:agents "$path"
fi