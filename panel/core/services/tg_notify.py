"""Puente de notificaciones a Telegram (§4.6), lado lógico y testeable. El loop
de Redis vive en workers/tg_bridge.py y solo llama a estas funciones.

- notify_request: envía la solicitud al topic del proyecto con teclado inline;
  si el topic fue borrado a mano (error 400), lo recrea y reintenta.
- notify_resolved: edita el mensaje con el desenlace y quita el teclado."""

from __future__ import annotations

import logging

from panel.core.models import Config, PermissionRequest
from panel.core.services import telegram as tg

log = logging.getLogger("tg_bridge")

OUTCOME_LABEL = {
    "allow": "✅ Permitido",
    "allow_always": "♾️ Permitido siempre",
    "deny": "⛔ Denegado",
    "timeout": "⌛ Expiró sin respuesta",
}


def _chat_id() -> str | None:
    return Config.get("tg_chat_id")


def _topic_for(project) -> int | None:
    """Topic del proyecto; si no tiene, el topic 'sistema'."""
    if project.telegram_topic_id:
        return project.telegram_topic_id
    sistema = Config.get("tg_sistema_topic")
    return int(sistema) if sistema else None


def _ensure_project_topic(project) -> int | None:
    """Recrea el topic del proyecto (borrado a mano) y lo persiste."""
    chat_id = _chat_id()
    if not chat_id:
        return None
    tid = tg.create_forum_topic(chat_id, project.name or project.slug)
    project.telegram_topic_id = tid
    project.save(update_fields=["telegram_topic_id", "updated_at"])
    return tid


def notify_request(request_id: str) -> None:
    chat_id = _chat_id()
    if not chat_id:
        return
    req = (
        PermissionRequest.objects.select_related("session__project")
        .filter(id=request_id)
        .first()
    )
    if req is None or req.status != PermissionRequest.Status.PENDING:
        return
    project = req.session.project
    text = tg.format_request(req)
    keyboard = tg.keyboard_for(str(req.id))
    topic = _topic_for(project)
    try:
        msg_id = tg.send_message(chat_id, text, thread_id=topic, reply_markup=keyboard)
    except tg.TelegramError as exc:
        # Topic borrado a mano → 400 "thread not found": recrear y reintentar.
        if exc.code == 400 and project.telegram_topic_id:
            topic = _ensure_project_topic(project)
            msg_id = tg.send_message(chat_id, text, thread_id=topic, reply_markup=keyboard)
        else:
            log.warning("sendMessage falló para %s: %s", request_id, exc)
            return
    PermissionRequest.objects.filter(id=req.id).update(tg_message_id=msg_id)


def notify_resolved(request_id: str, outcome: str) -> None:
    chat_id = _chat_id()
    if not chat_id:
        return
    req = PermissionRequest.objects.select_related("session__project").filter(id=request_id).first()
    if req is None or not req.tg_message_id:
        return
    project = req.session.project
    label = OUTCOME_LABEL.get(outcome, outcome)
    text = f"[{project.slug}] {req.tool}\n{req.input_preview}\n{label}"
    try:
        tg.edit_message_text(chat_id, req.tg_message_id, text)  # sin reply_markup → quita teclado
    except tg.TelegramError as exc:
        log.info("editMessageText no aplicado para %s: %s", request_id, exc)
