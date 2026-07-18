"""Cliente del Bot API de Telegram (§4.6). httpx síncrono: lo usan la vista del
webhook, el bridge y el provisioning. El token vive en settings (env), nunca en
disco de proyecto."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from django.conf import settings
from django.utils import timezone

# httpx loguea la URL completa (con el token del bot) a nivel INFO. Silenciar
# para que el token no aparezca en journald.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

TG_TEXT_LIMIT = 4096
CALLBACK_DATA_LIMIT = 64


class TelegramError(RuntimeError):
    def __init__(self, method: str, code: int | None, description: str) -> None:
        super().__init__(f"{method}: [{code}] {description}")
        self.code = code
        self.description = description


def _api(method: str, **params) -> Any:
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        raise TelegramError(method, None, "TELEGRAM_BOT_TOKEN no configurado")
    url = f"https://api.telegram.org/bot{token}/{method}"
    resp = httpx.post(url, json=params, timeout=15)
    data = resp.json()
    if not data.get("ok"):
        raise TelegramError(method, data.get("error_code"), data.get("description", "error"))
    return data.get("result", {})


def send_message(chat_id, text: str, *, thread_id=None, reply_markup=None) -> int:
    params: dict = {"chat_id": chat_id, "text": text[:TG_TEXT_LIMIT]}
    if thread_id is not None:
        params["message_thread_id"] = thread_id
    if reply_markup is not None:
        params["reply_markup"] = reply_markup
    return _api("sendMessage", **params).get("message_id")


def edit_message_text(chat_id, message_id: int, text: str, *, reply_markup=None) -> None:
    params: dict = {"chat_id": chat_id, "message_id": message_id, "text": text[:TG_TEXT_LIMIT]}
    # reply_markup ausente = se quita el teclado.
    if reply_markup is not None:
        params["reply_markup"] = reply_markup
    _api("editMessageText", **params)


def answer_callback_query(callback_query_id: str, text: str = "") -> None:
    _api("answerCallbackQuery", callback_query_id=callback_query_id, text=text[:200])


def create_forum_topic(chat_id, name: str) -> int:
    return _api("createForumTopic", chat_id=chat_id, name=name[:128]).get("message_thread_id")


def set_webhook(url: str, secret_token: str) -> None:
    _api(
        "setWebhook",
        url=url,
        secret_token=secret_token,
        allowed_updates=["callback_query"],
    )


def get_updates(timeout: int = 0) -> list[dict]:
    return _api("getUpdates", timeout=timeout)


# ---------- formato de solicitudes de permiso ----------

def keyboard_for(request_id: str) -> dict:
    """Inline keyboard [Permitir | Denegar] / [Permitir siempre]. callback_data =
    '<answer>:<uuid>' (≤64 bytes)."""
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Permitir", "callback_data": f"allow:{request_id}"},
                {"text": "⛔ Denegar", "callback_data": f"deny:{request_id}"},
            ],
            [{"text": "♾️ Permitir siempre", "callback_data": f"allow_always:{request_id}"}],
        ]
    }


def parse_callback_data(data: str) -> tuple[str, str] | None:
    """'<answer>:<uuid>' → (answer, uuid) o None si no casa."""
    answer, sep, uuid = (data or "").partition(":")
    if sep and answer in {"allow", "deny", "allow_always"} and uuid:
        return answer, uuid
    return None


def format_request(req) -> str:
    """[<slug>] <tool> + preview ≤500 + tiempo restante. Duro ≤4096."""
    remaining = int((req.expires_at - timezone.now()).total_seconds())
    remaining = max(remaining, 0)
    mins, secs = divmod(remaining, 60)
    text = (
        f"[{req.session.project.slug}] {req.tool}\n"
        f"{req.input_preview}\n"
        f"⏳ expira en {mins}m {secs}s"
    )
    return text[:TG_TEXT_LIMIT]


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)
