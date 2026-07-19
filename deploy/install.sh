#!/usr/bin/env bash
# Fase 0 — instalacion idempotente del VPS. Seguro de correr mas de una vez.
# Requiere: root, Ubuntu 24.04, LE_EMAIL en el entorno (o TTY interactivo).
#
# Sub-comandos:
#   (sin flag)              — instalación completa (idempotente)
#   --update                — actualiza código desde git, re-sincroniza deps,
#                             corre migraciones, collectstatic y reinicia
#                             panel.service. NO toca secretos, Docker, ni
#                             paquetes del sistema.
set -euo pipefail

REPO_DIR="/opt/panel"
PROJECTS_DIR="/srv/projects"
TTYD_DIR="${REPO_DIR}/deploy/ttyd"
REPO_URL="https://github.com/YOYO-DR/claude-code-hosted.git"

if [[ $EUID -ne 0 ]]; then
  echo "Este script debe correr como root." >&2
  exit 1
fi

# Rama --update: solo trae código nuevo y reinicia. Sale antes del install completo.
if [[ "${1:-}" == "--update" ]]; then
  echo "==> Modo --update: pull + sync + migrate + collectstatic + restart"
  if [[ ! -d "$REPO_DIR/.git" ]]; then
    echo "  /opt/panel no es un repo git. Clonando desde $REPO_URL ..." >&2
    git clone "$REPO_URL" "$REPO_DIR"
  fi
  cd "$REPO_DIR"
  git config --global --add safe.directory "$REPO_DIR" 2>/dev/null || true
  OLD_HEAD="$(git rev-parse --short HEAD 2>/dev/null || echo none)"
  echo "  HEAD antes: $OLD_HEAD"
  git fetch --all --prune
  # --ff-only: si divergió, abortar en vez de mezclar. Evita pisar commits
  # locales que el operador haya hecho a mano.
  git pull --ff-only
  NEW_HEAD="$(git rev-parse --short HEAD)"
  echo "  HEAD después: $NEW_HEAD"
  if [[ "$OLD_HEAD" == "$NEW_HEAD" ]]; then
    echo "  sin cambios nuevos."
  else
    echo "  commits nuevos:"
    git log --oneline "${OLD_HEAD}..${NEW_HEAD}" || true
  fi

  echo "==> uv sync"
  # Cargar /etc/panel/panel.env en el entorno. runuser -u <user> sin --login
  # hereda el entorno del padre (donde 'set -a; source' ya exportó todo),
  # pero depender de eso es frágil: si PAM limpia env, falla. Pasamos las
  # vars explícitamente con `env VAR=...` para garantizar.
  set -a
  # shellcheck disable=SC1091
  source /etc/panel/panel.env
  set +a

  # Empaquetar vars de panel.env en una sola cadena KEY=VAL KEY=VAL ... para
  # anteponerla al comando. set -a arriba ya las dejó exportadas, pero al
  # hacer 'env PANEL_X=... ... comando', env pisa con las que le pasamos y
  # runuser las ve. Usamos 'env' (no set) para que el orden de precedencia
  # sea claro.
  PANEL_ENV_ARGS=""
  while IFS='=' read -r k v; do
    # Saltar comentarios y líneas vacías.
    [[ -z "$k" || "$k" =~ ^[[:space:]]*# ]] && continue
    # Escapar comillas en v para que no rompan el eval posterior.
    v_esc="${v//\"/\\\"}"
    PANEL_ENV_ARGS+="\"$k=$v_esc\" "
  done < /etc/panel/panel.env

  runuser -u panel -- env HOME=/home/panel \
    $PANEL_ENV_ARGS \
    uv sync --project "$REPO_DIR" --frozen 2>&1 | tail -3 \
    || runuser -u panel -- env HOME=/home/panel \
       $PANEL_ENV_ARGS \
       uv sync --project "$REPO_DIR" 2>&1 | tail -3

  echo "==> Migraciones + collectstatic"
  cd "$REPO_DIR"
  runuser -u panel -- env HOME=/home/panel \
    $PANEL_ENV_ARGS \
    /opt/panel/.venv/bin/python manage.py migrate --noinput 2>&1 | tail -5
  runuser -u panel -- env HOME=/home/panel \
    $PANEL_ENV_ARGS \
    /opt/panel/.venv/bin/python manage.py collectstatic --noinput 2>&1 | tail -3

  echo "==> Reinicio de panel.service"
  systemctl restart panel.service
  if systemctl is-active --quiet panel.service; then
    echo "  panel.service: active"
  else
    echo "  AVISO: panel.service NO quedó activo tras restart. Revisa 'journalctl -u panel'." >&2
    exit 2
  fi
  echo "OK — update completo."
  exit 0
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
# Identidad git de los agentes (para commits en repos de GitHub, Fase 5).
runuser -u agents -- git config --global user.email "agente@claude-code-hosted.local" || true
runuser -u agents -- git config --global user.name "Agente Claude Code" || true
runuser -u agents -- git config --global --add safe.directory '*' || true

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
# Backup a S3/MinIO (Fase 6): vacío = solo backup local cifrado.
PANEL_BACKUP_S3_ENDPOINT=
PANEL_BACKUP_S3_BUCKET=
PANEL_BACKUP_S3_ACCESS_KEY=
PANEL_BACKUP_S3_SECRET_KEY=
PANEL_BACKUP_S3_REGION=us-east-1
PANEL_BACKUP_S3_PREFIX=panel/
PANEL_BACKUP_S3_RETENTION=14
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
