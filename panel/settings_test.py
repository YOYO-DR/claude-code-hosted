"""Settings de test: SQLite en memoria + channel layer en memoria. Sin
dependencias externas para la mayoría de los tests unitarios. Los tests que
necesitan Redis real lo piden por fixture."""

import os

os.environ.setdefault("PANEL_DEBUG", "1")
os.environ.setdefault("PANEL_SECRET_ENC_KEYS", "")

from panel.settings import *  # noqa: E402,F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}

# Una clave Fernet fija para tests de cifrado (no es un secreto real).
from cryptography.fernet import Fernet  # noqa: E402

SECRET_ENC_KEYS = [Fernet.generate_key().decode()]

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
