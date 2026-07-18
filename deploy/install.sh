#!/usr/bin/env bash
# Fase 0 — instalacion idempotente del VPS. Seguro de correr mas de una vez.
# Requiere: root, Ubuntu 24.04, LE_EMAIL en el entorno (o TTY interactivo).
set -euo pipefail

REPO_DIR="/opt/panel"
PROJECTS_DIR="/srv/projects"
TTYD_DIR="${REPO_DIR}/deploy/ttyd"

if [[ $EUID -ne 0 ]]; then
  echo "Este script debe correr como root." >&2
  exit 1
fi

if [[ -z "${LE_EMAIL:-}" ]]; then
  if [[ -t 0 ]]; then
    read -rp "Email de contacto para Let's Encrypt: " LE_EMAIL
  else
    echo "Falta LE_EMAIL en el entorno (email de contacto para Let's Encrypt) y no hay TTY para pedirlo." >&2
    exit 1
  fi
fi
[[ -z "$LE_EMAIL" ]] && { echo "LE_EMAIL no puede estar vacio." >&2; exit 1; }

echo "==> Paquetes base"
apt-get update -qq
apt-get install -y -qq \
  ca-certificates curl gnupg git jq tmux ttyd ufw apache2-utils openssl \
  python3.12 python3.12-venv

echo "==> Docker"
if ! command -v docker >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
fi
apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin
systemctl enable --now docker >/dev/null

echo "==> Node LTS + Claude Code CLI"
if ! command -v node >/dev/null 2>&1 || [[ "$(node -v | sed 's/^v//;s/\..*//')" -lt 22 ]]; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash - >/dev/null
  apt-get install -y -qq nodejs
fi
npm install -g --silent @anthropic-ai/claude-code

echo "==> uv"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
fi

echo "==> Usuarios de sistema"
id -u agents >/dev/null 2>&1 || useradd --create-home --shell /bin/bash agents
id -u panel  >/dev/null 2>&1 || useradd --create-home --shell /usr/sbin/nologin panel
usermod -aG docker agents

echo "==> Directorios"
install -d -o panel  -g panel  -m 0755 "$REPO_DIR"
install -d -o agents -g agents -m 0755 "$PROJECTS_DIR"
install -d -o root   -g root   -m 0755 "$TTYD_DIR"
[[ -f "${TTYD_DIR}/ports.json" ]] || echo '{}' > "${TTYD_DIR}/ports.json"

echo "==> Firewall"
ufw allow OpenSSH >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null

echo "==> LE_EMAIL persistido para compose.infra.yml"
install -d -m 0700 /etc/panel
printf 'LE_EMAIL=%s\n' "$LE_EMAIL" > /etc/panel/traefik.env
chmod 600 /etc/panel/traefik.env

echo "==> Password de Postgres"
if [[ ! -f /etc/panel/postgres_password.txt ]]; then
  openssl rand -base64 24 > /etc/panel/postgres_password.txt
  chmod 600 /etc/panel/postgres_password.txt
fi

echo "==> Credencial basicAuth de ttyd (escotilla de terminal)"
TTYD_USER="${TTYD_USER:-yoiner}"
if [[ ! -f /etc/panel/ttyd.htpasswd ]]; then
  if [[ -z "${TTYD_PASSWORD:-}" ]]; then
    TTYD_PASSWORD="$(openssl rand -base64 18)"
    GENERATED_TTYD_PASSWORD=1
  fi
  htpasswd -Bbc /etc/panel/ttyd.htpasswd "$TTYD_USER" "$TTYD_PASSWORD" >/dev/null
  chmod 600 /etc/panel/ttyd.htpasswd
fi

echo "OK — instalacion completa."
if [[ "${GENERATED_TTYD_PASSWORD:-0}" == "1" ]]; then
  echo "Credencial ttyd generada — guardala, no se vuelve a mostrar:"
  echo "  usuario:  $TTYD_USER"
  echo "  password: $TTYD_PASSWORD"
fi
