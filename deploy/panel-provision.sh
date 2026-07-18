#!/usr/bin/env bash
# Provisiona el directorio de un proyecto como root: mkdir + git init + chown a
# `agents` (el worker escribe código ahí) + render. Lo invoca el panel vía
# `sudo -n panel-provision.sh <slug> <path>`. El path debe estar bajo
# PROJECTS_ROOT (/srv/projects) — se valida para no chownear rutas arbitrarias.
set -euo pipefail

slug="${1:?uso: panel-provision.sh <slug> <path>}"
path="${2:?uso: panel-provision.sh <slug> <path>}"

case "$path" in
  /srv/projects/*) : ;;
  *) echo "path fuera de /srv/projects: $path" >&2; exit 2 ;;
esac
# slug solo [a-z0-9-] (defensa; el modelo ya usa SlugField)
if ! printf '%s' "$slug" | grep -qE '^[a-z0-9][a-z0-9-]*$'; then
  echo "slug inválido: $slug" >&2; exit 2
fi

mkdir -p "$path"
if [ ! -e "$path/.git" ]; then
  git init -q "$path"
fi
chown -R agents:agents "$path"

exec /opt/panel/deploy/panel-render.sh
