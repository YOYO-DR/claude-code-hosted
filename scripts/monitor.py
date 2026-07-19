#!/usr/bin/env python
"""Monitor de salud (§6.2). Corre periódico (monitor.timer) y alerta al topic
'sistema' de Telegram ante: disco >90%, worker en crash-loop (≥3 restarts), y
sesiones sin heartbeat (las marca `crashed` — estado honesto). Dedupe por
cooldown para no spamear."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

import django

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "panel.settings")
django.setup()

import redis  # noqa: E402
from django.conf import settings  # noqa: E402
from django.utils import timezone  # noqa: E402

from panel.core import bus  # noqa: E402
from panel.core.models import Config, Session  # noqa: E402
from panel.core.services import telegram as tg  # noqa: E402

log = logging.getLogger("monitor")

DISK_PCT_ALERT = 90
CRASHLOOP_RESTARTS = 3
HEARTBEAT_GRACE_S = 20  # margen tras arrancar antes de exigir heartbeat
ALERT_COOLDOWN_S = 1800  # 30 min por condición

ALIVE = [
    Session.Status.RUNNING,
    Session.Status.IDLE,
    Session.Status.WAITING_APPROVAL,
]


def _should_alert(key: str) -> bool:
    last = Config.get(f"alert:{key}")
    now = time.time()
    if last:
        try:
            if now - float(last) < ALERT_COOLDOWN_S:
                return False
        except ValueError:
            pass
    Config.set(f"alert:{key}", str(now))
    return True


def _alert(text: str) -> None:
    log.warning("ALERTA: %s", text)
    chat = Config.get("tg_chat_id")
    topic = Config.get("tg_sistema_topic")
    if not (chat and settings.TELEGRAM_BOT_TOKEN):
        return
    try:
        tg.send_message(chat, "🚨 " + text, thread_id=int(topic) if topic else None)
    except Exception as exc:  # noqa: BLE001
        log.warning("no se pudo alertar a Telegram: %s", exc)


def check_disk() -> None:
    st = os.statvfs("/")
    used_pct = 100.0 * (1 - st.f_bavail / st.f_blocks)
    if used_pct > DISK_PCT_ALERT and _should_alert("disk"):
        _alert(f"Disco al {used_pct:.0f}% en / (umbral {DISK_PCT_ALERT}%).")


def check_heartbeats(r: redis.Redis) -> None:
    cutoff = timezone.now() - timedelta(seconds=HEARTBEAT_GRACE_S)
    for s in Session.objects.filter(status__in=ALIVE, updated_at__lt=cutoff):
        try:
            hb = r.get(bus.key_heartbeat(str(s.id)))
        except redis.RedisError:
            return  # bus caído: no es asunto de este chequeo
        if hb is None:
            s.status = Session.Status.CRASHED
            s.ended_at = s.ended_at or timezone.now()
            s.save(update_fields=["status", "ended_at", "updated_at"])
            if _should_alert(f"hb:{s.id}"):
                _alert(f"Sesión {s.id} sin heartbeat → marcada crashed.")


def check_crashloops() -> None:
    res = subprocess.run(
        ["systemctl", "list-units", "claude-session@*", "--no-legend", "--plain", "--all"],
        capture_output=True, text=True,
    )
    for line in res.stdout.splitlines():
        parts = line.split()
        if not parts:
            continue
        unit = parts[0]
        show = subprocess.run(
            ["systemctl", "show", unit, "-p", "NRestarts", "--value"],
            capture_output=True, text=True,
        )
        try:
            nrestarts = int(show.stdout.strip() or "0")
        except ValueError:
            continue
        if nrestarts >= CRASHLOOP_RESTARTS and _should_alert(f"crashloop:{unit}"):
            _alert(f"Worker {unit} en crash-loop (NRestarts={nrestarts}).")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    r = redis.from_url(settings.REDIS_URL)
    check_disk()
    check_heartbeats(r)
    check_crashloops()
    r.close()


if __name__ == "__main__":
    main()
