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

echo "==> Desactivando el ttyd.service por defecto del paquete apt"
# El paquete Ubuntu trae su propio ttyd.service (puerto 7681, sin --writable,
# con -O login) auto-habilitado. Choca con nuestro pool de puertos propio
# (ttyd@.service, ver deploy/ttyd/) asi que lo enmascaramos.
systemctl disable --now ttyd.service >/dev/null 2>&1 || true
systemctl mask ttyd.service >/dev/null 2>&1 || true

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

echo "==> Directorio de secretos de la plataforma"
install -d -m 0700 /etc/panel

echo "==> Cert de origen (Cloudflare Origin CA)"
# El cert (cert.pem) + su key (key.pem) viven en /etc/panel/origin y los sirve
# Traefik como default cert (ver deploy/traefik/dynamic/tls.yml). Se generan
# fuera de este script (via API de Cloudflare Origin CA); aqui solo se asegura
# el directorio. Si faltan, se avisa pero no se aborta (util en primer arranque).
install -d -m 0700 /etc/panel/origin
if [[ ! -f /etc/panel/origin/cert.pem || ! -f /etc/panel/origin/key.pem ]]; then
  echo "  AVISO: falta /etc/panel/origin/{cert,key}.pem — Traefik servira su cert autofirmado hasta que se instalen." >&2
fi

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

echo "==> Dependencias Python del panel (uv sync en /opt/panel)"
if [[ -f /opt/panel/pyproject.toml ]]; then
  runuser -u panel -- env HOME=/home/panel uv sync --project /opt/panel --frozen 2>&1 | tail -3 || \
    runuser -u panel -- env HOME=/home/panel uv sync --project /opt/panel 2>&1 | tail -3
fi

echo "==> panel.env (config del panel Django y de los workers)"
# Secretos compartidos por panel.service y claude-session@.service. El token
# del modelo NO va aquí (se descifra de la DB en memoria del worker, §4.3).
if [[ ! -f /etc/panel/panel.env ]]; then
  PG_PW="$(cat /etc/panel/postgres_password.txt)"
  DJ_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(50))')"
  ENC_KEY="$(/opt/panel/.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())' 2>/dev/null \
             || python3 -c 'import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')"
  cat > /etc/panel/panel.env <<EOF
DJANGO_SETTINGS_MODULE=panel.settings
PANEL_DEBUG=0
PANEL_SECRET_KEY=${DJ_KEY}
PANEL_SECRET_ENC_KEYS=${ENC_KEY}
PANEL_ALLOWED_HOSTS=claude-code-hosted.yoyodr.dev
PANEL_CSRF_TRUSTED_ORIGINS=https://claude-code-hosted.yoyodr.dev
PANEL_DB_NAME=panel
PANEL_DB_USER=panel
PANEL_DB_PASSWORD=${PG_PW}
PANEL_DB_HOST=127.0.0.1
PANEL_DB_PORT=5432
PANEL_REDIS_URL=redis://127.0.0.1:6379/0
PANEL_PROJECTS_ROOT=/srv/projects
PANEL_AGENTS_HOME=/home/agents
PANEL_PUBLIC_BASE_URL=https://claude-code-hosted.yoyodr.dev
# Telegram (Fase 4): rellenar y correr 'manage.py tg_setup'. Vacío = sin Telegram.
PANEL_TELEGRAM_BOT_TOKEN=
PANEL_TELEGRAM_USER_IDS=
EOF
  # root:panel 640: el usuario panel lo lee (migrate/collectstatic manual);
  # los servicios systemd lo leen como root antes de bajar de privilegios.
  chown root:panel /etc/panel/panel.env
  chmod 640 /etc/panel/panel.env
fi

echo "==> sudoers para el panel (solo systemctl de claude-session@*)"
install -m 0440 -o root -g root /opt/panel/deploy/sudoers.d-panel /etc/sudoers.d/panel 2>/dev/null || true
visudo -cf /etc/sudoers.d/panel >/dev/null

echo "OK — instalacion completa."
if [[ "${GENERATED_TTYD_PASSWORD:-0}" == "1" ]]; then
  echo "Credencial ttyd generada — guardala, no se vuelve a mostrar:"
  echo "  usuario:  $TTYD_USER"
  echo "  password: $TTYD_PASSWORD"
fi
