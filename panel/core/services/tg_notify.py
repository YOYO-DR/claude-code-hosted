"""Puente de notificaciones a Telegram (§4.6), lado lógico y testeable. El loop
de Redis vive en workers/tg_bridge.py y solo llama a estas funciones.

- notify_request: envía la solicitud al topic del proyecto con teclado inline;
  si el topic fue borrado a mano (error 400), lo recrea y reintenta.
- notify_resolved: edita el mensaje con el desenlace y quita el teclado."""

from __future__ import annotations

import logging

from panel.core.models import Config, PermissionRequest
from panel.core.services import questions as q_svc
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
    # SP14: AskUserQuestion se renderiza con sus preguntas y un teclado de
    # opciones, no con Permitir/Denegar (que no significan nada aquí).
    questions = (
        q_svc.parse_questions(req.input_full) if req.tool == "AskUserQuestion" else []
    )
    if questions:
        text = tg.format_questions(req, questions)
        keyboard = tg.keyboard_for_questions(str(req.id), questions)
    else:
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
    # SP14: para AskUserQuestion resuelto, mostramos QUÉ se eligió en vez del
    # preview crudo (que es el JSON de las preguntas y no dice nada).
    questions = (
        q_svc.parse_questions(req.input_full) if req.tool == "AskUserQuestion" else []
    )
    if questions and outcome in {"allow", "allow_always"}:
        sel = read_selections(request_id)
        chosen = q_svc.summarize(questions, q_svc.build_answers(questions, sel or {}))
        body = chosen or "(sin selección registrada)"
        text = f"[{project.slug}] ❓ Respondido\n{body}"
    else:
        text = f"[{project.slug}] {req.tool}\n{req.input_preview}\n{label}"
    try:
        tg.edit_message_text(chat_id, req.tg_message_id, text)  # sin reply_markup → quita teclado
    except tg.TelegramError as exc:
        log.info("editMessageText no aplicado para %s: %s", request_id, exc)
    clear_selections(request_id)


# ---------- SP14: selecciones parciales de AskUserQuestion (Telegram) ----------
# Viven en Redis mientras el usuario va marcando opciones en el teclado inline.
# Se borran al resolver. La fuente de verdad de los LABELS sigue siendo el
# input_full de la BD — aquí solo guardamos índices.

def _redis():
    import redis
    from django.conf import settings

    return redis.from_url(settings.REDIS_URL)


def read_selections(request_id: str) -> dict:
    import json

    from panel.core import bus

    client = _redis()
    try:
        raw = client.get(bus.key_perm_selections(request_id))
    except Exception:  # noqa: BLE001 — Redis caído no debe tumbar el bridge
        return {}
    finally:
        client.close()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
    except (ValueError, UnicodeDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def write_selections(request_id: str, selections: dict) -> None:
    import json

    from panel.core import bus

    client = _redis()
    try:
        client.set(
            bus.key_perm_selections(request_id),
            json.dumps(selections, ensure_ascii=False),
            ex=bus.ANSWER_TTL,
        )
    except Exception:  # noqa: BLE001
        pass
    finally:
        client.close()


def clear_selections(request_id: str) -> None:
    from panel.core import bus

    client = _redis()
    try:
        client.delete(bus.key_perm_selections(request_id))
    except Exception:  # noqa: BLE001
        pass
    finally:
        client.close()


def toggle_selection(questions: list[dict], selections: dict, qi: int, oi: int) -> dict:
    """Aplica un tap del teclado. Single-select reemplaza (y des-selecciona si
    se vuelve a tocar la misma); multiSelect togglea."""
    if qi < 0 or qi >= len(questions):
        return selections
    q = questions[qi]
    if oi < 0 or oi >= len(q["options"]):
        return selections
    key = str(qi)
    cur = selections.get(key) or selections.get(qi) or []
    if isinstance(cur, int):
        cur = [cur]
    cur = list(cur)
    if q.get("multiSelect"):
        cur = [x for x in cur if x != oi] if oi in cur else cur + [oi]
    else:
        cur = [] if cur[:1] == [oi] else [oi]
    out = {k: v for k, v in selections.items() if str(k) != key}
    out[key] = cur
    return out


def refresh_question_message(request_id: str, selections: dict) -> None:
    """Re-pinta el mensaje de Telegram con el estado actual de la selección."""
    chat_id = _chat_id()
    if not chat_id:
        return
    req = (
        PermissionRequest.objects.select_related("session__project")
        .filter(id=request_id)
        .first()
    )
    if req is None or not req.tg_message_id:
        return
    questions = q_svc.parse_questions(req.input_full)
    if not questions:
        return
    try:
        tg.edit_message_text(
            chat_id,
            req.tg_message_id,
            tg.format_questions(req, questions, selections),
            reply_markup=tg.keyboard_for_questions(request_id, questions, selections),
        )
    except tg.TelegramError as exc:
        # 400 "message is not modified" es normal (doble tap en la misma opción).
        log.info("refresh de preguntas no aplicado para %s: %s", request_id, exc)
