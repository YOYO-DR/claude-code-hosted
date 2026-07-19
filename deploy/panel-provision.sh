#!/usr/bin/env bash
# Provisiona el directorio de un proyecto como root: mkdir + git init + chown a
# `agents` (el worker escribe código ahí) + render. Lo invoca el panel vía
# `sudo -n panel-provision.sh <slug> <path>`. El path debe estar bajo
# PROJECTS_ROOT (/srv/projects) — se valida para no chownear rutas arbitrarias.
#
# Subcomandos:
#   provision <slug> <path>           — mkdir + git init + chown + render (default)
#   write-agents <path>               — escribe AGENTS.md desde stdin (cat > file)
#   remove-agents <path>              — borra AGENTS.md si existe
# Sin subcomando o argv vacío → comportamiento provision legacy (compat).
set -euo pipefail

cmd="${1:-provision}"

# Validación común: path bajo /srv/projects, slug saneado.
check_path() {
  case "$1" in
    /srv/projects/*) : ;;
    *) echo "path fuera de /srv/projects: $1" >&2; exit 2 ;;
  esac
}
check_slug() {
  if ! printf '%s' "$1" | grep -qE '^[a-z0-9][a-z0-9-]*$'; then
    echo "slug inválido: $1" >&2; exit 2
  fi
}

case "$cmd" in
  provision)
    slug="${2:?uso: panel-provision.sh provision <slug> <path>}"
    path="${3:?uso: panel-provision.sh provision <slug> <path>}"
    check_slug "$slug"
    check_path "$path"
    mkdir -p "$path"
    if [ ! -e "$path/.git" ]; then
      git init -q "$path"
    fi
    chown -R agents:agents "$path"
    exec /opt/panel/deploy/panel-render.sh
    ;;
  write-agents)
    path="${2:?uso: panel-provision.sh write-agents <path>}"
    check_path "$path"
    mkdir -p "$path"
    # Lee AGENTS.md desde stdin (el panel lo genera en Python).
    cat > "$path/AGENTS.md"
    chown agents:agents "$path/AGENTS.md"
    ;;
  remove-agents)
    path="${2:?uso: panel-provision.sh remove-agents <path>}"
    check_path "$path"
    rm -f "$path/AGENTS.md"
    ;;
  *)
    echo "subcomando desconocido: $cmd" >&2
    exit 2
    ;;
esac
