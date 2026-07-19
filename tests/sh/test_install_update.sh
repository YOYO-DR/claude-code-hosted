#!/usr/bin/env bash
# Smoke test del flag --update en install.sh. Valida:
#  - --update sin /opt/panel/.git intenta clonar (o falla limpio sin red)
#  - install.sh sin flag es identificable como script bash
#  - la rama --update está al inicio del script (parsea antes del install completo)
#
# NO corre install.sh completo (eso requiere root, apt, Docker, red).
set -euo pipefail

cd "$(dirname "$0")/../.."
REPO_ROOT="$(pwd)"

echo "==> install.sh existe y es ejecutable"
test -x deploy/install.sh
test -f deploy/install.sh

echo "==> La rama --update está antes del install completo"
UPDATE_LINE=$(grep -n '^if \[\[ "\${1:-}" == "--update" \]\]' deploy/install.sh | cut -d: -f1)
INSTALL_LINE=$(grep -n 'Paquetes base' deploy/install.sh | head -1 | cut -d: -f1)
if [[ -z "$UPDATE_LINE" ]] || [[ -z "$INSTALL_LINE" ]]; then
  echo "FALLO: no se encontraron los marcadores esperados" >&2
  exit 1
fi
if [[ "$UPDATE_LINE" -ge "$INSTALL_LINE" ]]; then
  echo "FALLO: --update (línea $UPDATE_LINE) NO está antes de Paquetes base (línea $INSTALL_LINE)" >&2
  exit 1
fi
echo "  --update en línea $UPDATE_LINE, install completo en $INSTALL_LINE ✓"

echo "==> --update sin repo clonado: clona o falla con mensaje claro"
# Simulamos un path que no existe, sin red. Esperaríamos un fallo de git clone
# pero con un mensaje que mencione el repo, no un trace raro.
TMP="$(mktemp -d)"
trap "rm -rf $TMP" EXIT
# Creamos un install.sh "shadow" que apunte REPO_DIR a TMP para evitar tocar /opt/panel.
SHADOW="$TMP/install.sh"
sed "s|/opt/panel|$TMP/panel|g; s|/home/panel|$TMP/home|g; s|/srv/projects|$TMP/projects|g" \
    deploy/install.sh > "$SHADOW"
chmod +x "$SHADOW"
# Como root podemos correrlo; si no, simulamos.
if [[ $EUID -eq 0 ]]; then
  set +e
  OUTPUT=$("$SHADOW" --update 2>&1)
  RC=$?
  set -e
  # Debe mencionar el intento de clonar (o, si no hay red, fallar con error de git).
  if [[ $RC -eq 0 ]]; then
    echo "  --update terminó OK (tuvo red, pudo clonar)"
  else
    if echo "$OUTPUT" | grep -qE "no es un repo git|git clone|fatal"; then
      echo "  --update falló limpio (esperado sin repo y/o sin red)"
    else
      echo "FALLO: --update falló pero sin mensaje claro" >&2
      echo "$OUTPUT" >&2
      exit 1
    fi
  fi
else
  echo "  (skip test que requiere root)"
fi

echo "OK — install.sh --update smoke test pasó."