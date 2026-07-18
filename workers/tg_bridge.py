"""tg_bridge (§4.6): psubscribe session:*:perm → envía la solicitud al topic de
Telegram; subscribe perm:resolved → edita el mensaje con el desenlace. Un solo
proceso (tg-bridge.service). Resiliente a Redis caído (reintenta)."""

from __future__ import annotations

import json
import logging
import os
import time

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "panel.settings")
django.setup()

import redis  # noqa: E402
from django.conf import settings  # noqa: E402

from panel.core import bus  # noqa: E402
from panel.core.services import tg_notify  # noqa: E402

log = logging.getLogger("tg_bridge")


def _handle(channel: str, data: str) -> None:
    try:
        payload = json.loads(data)
    except (json.JSONDecodeError, TypeError):
        return
    if channel == bus.key_perm_resolved():
        rid = payload.get("request_id")
        if rid:
            tg_notify.notify_resolved(rid, payload.get("outcome", ""))
    elif channel.endswith(":perm"):
        rid = payload.get("id")
        if rid:
            tg_notify.notify_request(rid)


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not settings.TELEGRAM_BOT_TOKEN:
        log.warning("TELEGRAM_BOT_TOKEN vacío; tg_bridge no hará nada útil")
    while True:
        try:
            r = redis.from_url(settings.REDIS_URL)
            ps = r.pubsub(ignore_subscribe_messages=True)
            ps.psubscribe("session:*:perm")
            ps.subscribe(bus.key_perm_resolved())
            log.info("tg_bridge suscrito a session:*:perm y perm:resolved")
            for msg in ps.listen():
                if msg.get("type") not in ("message", "pmessage"):
                    continue
                channel = msg["channel"]
                if isinstance(channel, bytes):
                    channel = channel.decode()
                data = msg["data"]
                if isinstance(data, bytes):
                    data = data.decode()
                try:
                    _handle(channel, data)
                except Exception as exc:  # noqa: BLE001 — un fallo no debe tumbar el loop
                    log.warning("error manejando %s: %s", channel, exc)
        except redis.RedisError as exc:
            log.warning("Redis no disponible (%s); reintentando en 2s", exc)
            time.sleep(2)


if __name__ == "__main__":
    run()
