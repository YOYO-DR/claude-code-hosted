#!/usr/bin/env bash
# SP4: hard-delete del directorio de un proyecto. rm -rf con validación estricta
# para que un atacante (o un bug del panel) no pueda borrar /etc, /home o
# cualquier cosa fuera de PROJECTS_ROOT. El panel lo invoca vía
# `sudo -n panel-purge.sh <path>` — sudoers whitelistea este binario.
set -euo pipefail

ROOT="${PANEL_PROJECTS_ROOT:-/srv/projects}"
path="${1:?uso: panel-purge.sh <path>}"

# Validación 1: debe ser path absoluto bajo $ROOT.
case "$path" in
  "$ROOT"/*) : ;;
  "$ROOT")   echo "refusing to purge PROJECTS_ROOT itself: $path" >&2; exit 2 ;;
  *)         echo "path fuera de $ROOT: $path" >&2; exit 2 ;;
esac

# Validación 2: no debe contener `..` (defensa adicional anti-traversal).
case "$path" in
  *..*) echo "path con '..' rechazado: $path" >&2; exit 2 ;;
esac

# Validación 3: debe ser directorio (no archivo suelto).
if [ ! -d "$path" ] && [ -e "$path" ]; then
  echo "no es un directorio: $path" >&2
  exit 2
fi

# Idempotente: no falla si no existe (delete ya completado).
if [ ! -e "$path" ]; then
  echo "ya no existe, nada que borrar: $path"
  exit 0
fi

rm -rf "$path"
echo "purged: $path"