"""Setup de Telegram (§4.6, una vez): captura el chat_id del grupo vía
getUpdates, crea el topic 'sistema', genera un secret y registra el webhook.

Requiere que haya un mensaje reciente en el grupo (para getUpdates). Corre como
root (lee panel.env):  sudo -E python manage.py tg_setup
"""

from __future__ import annotations

import secrets

from django.conf import settings
from django.core.management.base import BaseCommand

from panel.core.models import Config
from panel.core.services import telegram as tg


class Command(BaseCommand):
    help = "Configura Telegram: captura chat_id, crea topic sistema, registra webhook."

    def add_arguments(self, parser):
        parser.add_argument(
            "--chat-id",
            help="Forzar el chat_id del grupo (si getUpdates no lo encuentra).",
        )

    def handle(self, *args, **opts):
        if not settings.TELEGRAM_BOT_TOKEN:
            self.stderr.write("PANEL_TELEGRAM_BOT_TOKEN no configurado.")
            return

        chat_id = opts.get("chat_id") or self._capture_chat_id()
        if not chat_id:
            self.stderr.write(
                "No se pudo capturar chat_id. Envía un mensaje en el grupo y reintenta, "
                "o pásalo con --chat-id."
            )
            return
        Config.set("tg_chat_id", str(chat_id))
        self.stdout.write(f"chat_id = {chat_id}")

        # Topic 'sistema' para alertas de proyectos sin topic propio.
        if not Config.get("tg_sistema_topic"):
            try:
                tid = tg.create_forum_topic(chat_id, "sistema")
                Config.set("tg_sistema_topic", str(tid))
                self.stdout.write(f"topic sistema = {tid}")
            except tg.TelegramError as exc:
                self.stderr.write(f"no se pudo crear topic sistema: {exc}")

        # Webhook con secret aleatorio persistido.
        secret = Config.get("tg_webhook_secret") or secrets.token_urlsafe(32)
        Config.set("tg_webhook_secret", secret)
        url = settings.PUBLIC_BASE_URL.rstrip("/") + "/tg/webhook"
        try:
            tg.set_webhook(url, secret)
            self.stdout.write(f"webhook registrado en {url}")
        except tg.TelegramError as exc:
            self.stderr.write(f"setWebhook falló: {exc}")

    def _capture_chat_id(self):
        """Busca un chat de tipo group/supergroup en los updates recientes."""
        try:
            updates = tg.get_updates()
        except tg.TelegramError as exc:
            self.stderr.write(f"getUpdates falló (¿webhook activo?): {exc}")
            return None
        for upd in reversed(updates):
            for key in ("message", "channel_post", "my_chat_member", "edited_message"):
                obj = upd.get(key) or {}
                chat = obj.get("chat") or {}
                if chat.get("type") in ("group", "supergroup"):
                    return chat.get("id")
        return None
