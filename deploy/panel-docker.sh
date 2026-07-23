#!/usr/bin/env bash
# SP15: vista de contenedores Docker. El panel corre como `panel` (que NO está
# en el grupo docker — estar en él equivale a root). Delega aquí vía
# `sudo -n panel-docker.sh <subcomando>`; sudoers whitelistea este binario.
#
# Subcomandos:
#   list           — docker ps -a de todos los contenedores, una línea JSON por
#                    contenedor (incluye labels de compose).
#   stop <id>      — docker stop de UN contenedor. Nunca rm, nunca down, nunca -v:
#                    los datos y los volúmenes se quedan intactos.
#
# Invariante: los contenedores del compose de la propia plataforma
# (panel-infra: postgres/redis/traefik) NO se pueden parar desde aquí —
# pararlos tumbaría el panel que está emitiendo la orden.
set -euo pipefail

# Proyecto compose de la infra del panel. Se puede sobreescribir por env para
# despliegues que usen otro nombre.
PROTECTED_PROJECT="${PANEL_INFRA_PROJECT:-panel-infra}"
STOP_TIMEOUT="${PANEL_DOCKER_STOP_TIMEOUT:-15}"

cmd="${1:?uso: panel-docker.sh list|stop <id>}"

case "$cmd" in
  list)
    # Una línea JSON por contenedor. Construimos el objeto con --format en vez
    # de `{{json .}}` para fijar el contrato de campos (que cambia entre
    # versiones de Docker) y para incluir las labels de compose, que
    # `{{json .}}` no expone de forma estable.
    docker ps -a --no-trunc --format \
      '{"id":"{{.ID}}","name":"{{.Names}}","state":"{{.State}}","status":"{{.Status}}","image":"{{.Image}}","project":"{{.Label "com.docker.compose.project"}}","service":"{{.Label "com.docker.compose.service"}}","ports":"{{.Ports}}","created":"{{.CreatedAt}}"}'
    ;;

  stop)
    id="${2:?uso: panel-docker.sh stop <id|name>}"

    # Validación 1: charset. Los IDs de Docker son hex; los nombres admiten
    # [a-zA-Z0-9][a-zA-Z0-9_.-]*. Cualquier otra cosa (espacios, $, ;, backticks)
    # se rechaza antes de tocar docker.
    if ! printf '%s' "$id" | grep -Eq '^[a-zA-Z0-9][a-zA-Z0-9_.-]*$'; then
      echo "identificador de contenedor inválido: $id" >&2
      exit 2
    fi

    # Validación 2: no permitir parar la infra del panel. Resolvemos la label
    # del contenedor REAL (no confiamos en lo que diga el llamador).
    proj="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$id" 2>/dev/null || true)"
    if [ "$proj" = "$PROTECTED_PROJECT" ]; then
      echo "rechazado: '$id' pertenece a '$PROTECTED_PROJECT' (infra del panel)" >&2
      exit 3
    fi

    # `docker stop` manda SIGTERM y espera; tras el timeout, SIGKILL. No borra
    # el contenedor ni sus volúmenes — se puede volver a arrancar con `start`.
    docker stop --time "$STOP_TIMEOUT" "$id"
    ;;

  *)
    echo "subcomando desconocido: $cmd (esperado: list|stop)" >&2
    exit 2
    ;;
esac
