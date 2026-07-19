#!/usr/bin/env bash
# Clona un repo de GitHub en el dir del proyecto como root, crea la rama de
# trabajo y chownea a `agents`. El TOKEN llega por STDIN (no por argv, no a
# disco) y se inyecta con http.extraHeader (no queda en .git/config). Lo invoca
# el panel vía `sudo -n panel-clone.sh <path> <owner/repo> <branch>`.
set -euo pipefail

path="${1:?uso: panel-clone.sh <path> <owner/repo> <branch>}"
repo="${2:?uso: panel-clone.sh <path> <owner/repo> <branch>}"
branch="${3:?uso: panel-clone.sh <path> <owner/repo> <branch>}"

case "$path" in
  /srv/projects/*) : ;;
  *) echo "path fuera de /srv/projects: $path" >&2; exit 2 ;;
esac
if ! printf '%s' "$repo" | grep -qE '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$'; then
  echo "repo inválido: $repo" >&2; exit 2
fi

read -r token
b64=$(printf 'x-access-token:%s' "$token" | base64 -w0)
unset token

rm -rf "$path"
git -c http.extraHeader="AUTHORIZATION: basic ${b64}" clone --quiet "https://github.com/${repo}.git" "$path"
git -C "$path" checkout -q -B "$branch"
# Higiene: asegurar que el remoto NO lleva credenciales.
git -C "$path" remote set-url origin "https://github.com/${repo}.git"
chown -R agents:agents "$path"

exec /opt/panel/deploy/panel-render.sh
