"""Config de Django dirigida por entorno. Un solo archivo; los tests importan
de aquí y sobrescriben en settings_test.py. Nada de secretos por defecto en
código: en runtime vienen del EnvironmentFile del systemd unit."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _env(key: str, default: str | None = None, *, required: bool = False) -> str:
    val = os.environ.get(key, default)
    if required and not val:
        raise RuntimeError(f"Falta la variable de entorno requerida: {key}")
    return val or ""


DEBUG = _env("PANEL_DEBUG", "0") == "1"
SECRET_KEY = _env("PANEL_SECRET_KEY", "dev-insecure-key" if DEBUG else "", required=not DEBUG)

ALLOWED_HOSTS = [h for h in _env("PANEL_ALLOWED_HOSTS", "*" if DEBUG else "").split(",") if h]
CSRF_TRUSTED_ORIGINS = [o for o in _env("PANEL_CSRF_TRUSTED_ORIGINS", "").split(",") if o]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_otp",
    "django_otp.plugins.otp_totp",
    "channels",
    "panel.core",
    "panel.ui",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django_otp.middleware.OTPMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "panel.urls"
WSGI_APPLICATION = None
ASGI_APPLICATION = "panel.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": _env("PANEL_DB_NAME", "panel"),
        "USER": _env("PANEL_DB_USER", "panel"),
        "PASSWORD": _env("PANEL_DB_PASSWORD", ""),
        "HOST": _env("PANEL_DB_HOST", "127.0.0.1"),
        "PORT": _env("PANEL_DB_PORT", "5432"),
    }
}

REDIS_URL = _env("PANEL_REDIS_URL", "redis://127.0.0.1:6379/0")

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [REDIS_URL]},
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
]

LANGUAGE_CODE = "es"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "session_list"

# Clave(s) Fernet para cifrar secretos en DB (MultiFernet: la 1ª cifra, todas
# descifran — permite rotación). Formato: claves separadas por coma.
SECRET_ENC_KEYS = [k for k in _env("PANEL_SECRET_ENC_KEYS", "").split(",") if k]

# Rutas de la plataforma en el VPS.
PROJECTS_ROOT = Path(_env("PANEL_PROJECTS_ROOT", "/srv/projects"))
AGENTS_HOME = Path(_env("PANEL_AGENTS_HOME", "/home/agents"))

# Timeout por defecto de aprobaciones de permisos (segundos). Fase 3.
PERMISSION_TIMEOUT_SECONDS = int(_env("PANEL_PERMISSION_TIMEOUT", "900"))
