"""Permisos mixtos y aprobaciones (§4.1/§4.2). El worker crea un
PermissionRequest, lo publica en `session:<sid>:perm`, y espera la respuesta en
`perm:<uuid>:answer` (STRING SET NX EX). Cualquier origen (web/Telegram/timeout)
resuelve escribiendo esa clave con NX: el primero gana, el resto recibe
conflicto. La transición de estado del PermissionRequest la hace UN solo
escritor —el worker— al leer la respuesta, así es idempotente."""

from __future__ import annotations

import json
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from panel.core import bus
from panel.core.models import PermissionRequest, Session

PREVIEW_MAX = 500


def timeout_seconds(session: Session) -> int:
    return session.project.permission_timeout_seconds or settings.PERMISSION_TIMEOUT_SECONDS


def make_preview(tool: str, input_data: dict) -> str:
    text = f"{tool} {json.dumps(input_data, ensure_ascii=False, sort_keys=True)}"
    return text[:PREVIEW_MAX]


def create_request(
    session: Session, tool: str, input_data: dict, timeout_s: int
) -> PermissionRequest:
    now = timezone.now()
    return PermissionRequest.objects.create(
        session=session,
        tool=tool,
        input_full=input_data,
        input_preview=make_preview(tool, input_data),
        status=PermissionRequest.Status.PENDING,
        expires_at=now + timedelta(seconds=timeout_s),
    )


def serialize_request(req: PermissionRequest) -> dict:
    out = {
        "id": str(req.id),
        "session": str(req.session_id),
        "tool": req.tool,
        "input_preview": req.input_preview,
        "status": req.status,
        "expires_at": req.expires_at.isoformat(),
    }
    # SP9.1: las preguntas del agente (Claude Code AskUserQuestion tool)
    # llegan con input_full estructurado. Lo propagamos para que la SPA
    # pueda renderizar las opciones como botones cliqueables (no solamente
    # el preview). Para Bash/Edit/Write el input_full suele ser un command
    # string y exponerlo duplica info — solo lo enviamos en preguntas.
    if req.tool == "AskUserQuestion" and isinstance(req.input_full, dict):
        out["input_full"] = req.input_full
    return out


def apply_answer(
    req: PermissionRequest,
    answer: str,
    *,
    source: str = "web",
    always_rules: list[str] | None = None,
) -> None:
    """Transición idempotente del PermissionRequest según la respuesta ganadora.
    `source` (web|telegram) fija resolved_by para allow/deny; timeout siempre es
    TIMEOUT. `allow_always` persiste la(s) regla(s) y dispara re-render."""
    if req.status != PermissionRequest.Status.PENDING:
        return  # ya resuelto: idempotente
    status_map = {
        "allow": PermissionRequest.Status.ALLOWED,
        "allow_always": PermissionRequest.Status.ALLOWED_ALWAYS,
        "deny": PermissionRequest.Status.DENIED,
        "timeout": PermissionRequest.Status.EXPIRED,
    }
    if answer == "timeout":
        resolved_by = PermissionRequest.ResolvedBy.TIMEOUT
    elif source == "telegram":
        resolved_by = PermissionRequest.ResolvedBy.TELEGRAM
    else:
        resolved_by = PermissionRequest.ResolvedBy.WEB
    req.status = status_map[answer]
    req.resolved_by = resolved_by
    req.save(update_fields=["status", "resolved_by", "updated_at"])
    if answer == "allow_always":
        _persist_always_rules(req, always_rules or [req.tool])


def _persist_always_rules(req: PermissionRequest, rules: list[str]) -> None:
    """Añade las reglas a la allowlist de la policy del proyecto para que futuras
    SESIONES no vuelvan a preguntar (el worker pasa `allowed_tools` desde la DB).
    En la sesión ACTUAL el efecto lo da `updated_permissions` (SDK). El re-render
    de settings.json es best-effort: el worker corre como `agents` y no puede
    invocar el helper de render (sudo es solo para `panel`); la DB es la fuente
    de verdad de todos modos."""
    import logging

    from panel.core.services import privileged

    policy = req.session.project.permission_policy
    allowed = list(policy.allowed_tools or [])
    changed = False
    for rule in rules:
        if rule and rule not in allowed:
            allowed.append(rule)
            changed = True
    if changed:
        policy.allowed_tools = allowed
        policy.save(update_fields=["allowed_tools", "updated_at"])
    try:
        privileged.run_render()
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("session_worker").info(
            "render tras allow_always no disponible (%s); DB ya persistida", exc
        )


def expire_pending(session: Session) -> int:
    """Marca como expired todo request pendiente de esta sesión. Se llama al
    arrancar el worker: un request pendiente de un worker muerto jamás debe
    quedar aprobable (Gate 3)."""
    return PermissionRequest.objects.filter(
        session=session, status=PermissionRequest.Status.PENDING
    ).update(
        status=PermissionRequest.Status.EXPIRED,
        resolved_by=PermissionRequest.ResolvedBy.TIMEOUT,
    )


def cancel_pending_for_session(
    session: Session, *, resolved_by: str = PermissionRequest.ResolvedBy.TIMEOUT
) -> int:
    """Cancela en cascada todas las requests pendientes de una sesión que pasa
    a estado terminal (stopped/crashed). Mismo path que expire_pending pero
    parametrizable (útil cuando el caller ya tiene un resolved_by específico).
    Devuelve el nº de filas afectadas."""
    return PermissionRequest.objects.filter(
        session=session, status=PermissionRequest.Status.PENDING
    ).update(
        status=PermissionRequest.Status.EXPIRED,
        resolved_by=resolved_by,
    )


# Estados de sesión en los que una request pendiente sigue siendo aprobable.
# Una sesión muerta (stopped/crashed) o terminando (starting) no debe tener
# approvals visibles. Usado por la query única de cola (D11).
LIVE_SESSION_STATUSES = (
    Session.Status.RUNNING,
    Session.Status.WAITING_APPROVAL,
    Session.Status.IDLE,
)


def live_pending_qs():
    """QuerySet único de PermissionRequest que la UI debe mostrar (D11). Filtra:
    - status='pending' (no resueltas)
    - expires_at > now() (no vencidas en el reloj)
    - session.status ∈ {running, waiting_approval, idle} (sesión viva)

    Usado por `permission_queue` (vista) y `pending_permissions` (badge navbar)
    para que no diverjan (no más "doble fuente").
    """
    now = timezone.now()
    return PermissionRequest.objects.filter(
        status=PermissionRequest.Status.PENDING,
        expires_at__gt=now,
        session__status__in=LIVE_SESSION_STATUSES,
    )


def resolve_atomically(
    request_id: str, answer: str, *, source: str = "web"
) -> tuple[bool, PermissionRequest | None]:
    """Resuelve una PermissionRequest transaccionalmente y de forma idempotente
    (D11 / MIGRATION1 §2.2). Devuelve (claimed, req):

    - `claimed=True` cuando el UPDATE afectó exactamente 1 fila: este caller
      gana; el worker será notificado por el caller vía `SET NX` en Redis.
    - `claimed=False` cuando afectó 0 filas: otro origen ya respondió (o el
      request no existe), o la sesión ya no está viva — no se hace nada.

    Acepta `allow`, `deny`, `allow_always`, `timeout`. `source` solo aplica a
    allow/deny/allow_always (timeout siempre lleva `resolved_by=TIMEOUT`).

    Se ejecuta dentro de `transaction.atomic` con `select_for_update(skip_locked)`
    para que dos resoluciones concurrentes no se pisen: la segunda ve la fila
    ya cambiada y devuelve `claimed=False`.
    """
    from django.db import transaction

    status_map = {
        "allow": PermissionRequest.Status.ALLOWED,
        "allow_always": PermissionRequest.Status.ALLOWED_ALWAYS,
        "deny": PermissionRequest.Status.DENIED,
        "timeout": PermissionRequest.Status.EXPIRED,
    }
    if answer not in status_map:
        raise ValueError(f"respuesta inválida: {answer}")
    new_status = status_map[answer]
    if answer == "timeout":
        resolved_by = PermissionRequest.ResolvedBy.TIMEOUT
    elif source == "telegram":
        resolved_by = PermissionRequest.ResolvedBy.TELEGRAM
    else:
        resolved_by = PermissionRequest.ResolvedBy.WEB

    with transaction.atomic():
        try:
            req = (
                PermissionRequest.objects.select_for_update(skip_locked=True)
                .get(id=request_id, status=PermissionRequest.Status.PENDING)
            )
        except PermissionRequest.DoesNotExist:
            return False, None
        # Defensa en profundidad: si la sesión ya no está viva, no dejamos
        # aprobar fantasmas aunque la fila siga pending en DB.
        if req.session.status not in LIVE_SESSION_STATUSES:
            # Marcamos expired aquí mismo y devolvemos claimed=False (el caller
            # verá que no ganó). Esto cierra el caso "sesión murió con pending".
            req.status = PermissionRequest.Status.EXPIRED
            req.resolved_by = PermissionRequest.ResolvedBy.TIMEOUT
            req.save(update_fields=["status", "resolved_by", "updated_at"])
            return False, req
        req.status = new_status
        req.resolved_by = resolved_by
        req.save(update_fields=["status", "resolved_by", "updated_at"])
        return True, req


def claim_answer_sync(
    redis_client,
    request_id: str,
    answer: str,
    source: str = "web",
    option_index: int | None = None,
) -> bool:
    """Escribe la respuesta con SET NX, codificando el origen como
    `answer|source[|opt:N]`. El último token solo aparece si la SPA eligió
    una opción de AskUserQuestion (SP9.1)."""
    if answer not in {"allow", "deny", "allow_always"}:
        raise ValueError(f"respuesta inválida: {answer}")
    if option_index is not None:
        value = f"{answer}|{source}|opt:{option_index}"
    else:
        value = f"{answer}|{source}"
    return bool(redis_client.set(bus.key_answer(request_id), value, nx=True, ex=bus.ANSWER_TTL))


def _split_answer(raw: str | None) -> tuple[str, str, int | None]:
    """`answer|source[|opt:N]` → (answer, source, option_index). Tolera
    valores legacy sin origen ni option."""
    if not raw:
        return "timeout", "web", None
    parts = raw.split("|")
    answer = parts[0] or "timeout"
    source = parts[1] if len(parts) > 1 else "web"
    option_index: int | None = None
    if len(parts) > 2 and parts[2].startswith("opt:"):
        try:
            option_index = int(parts[2].split(":", 1)[1])
        except ValueError:
            option_index = None
    return answer, source, option_index


POLL_INTERVAL = 0.5  # s; cada cuánto sondea el worker la respuesta


async def _wait_answer(aredis, request_id: str, timeout_s: int, poll_interval: float) -> str | None:
    import asyncio
    import time

    key = bus.key_answer(request_id)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        val = await aredis.get(key)
        if val is not None:
            return val.decode() if isinstance(val, bytes) else val
        await asyncio.sleep(poll_interval)
    return None


async def request_and_wait(
    session: Session,
    tool_name: str,
    input_data: dict,
    *,
    aredis,
    hooks=None,
    always_rules: list[str] | None = None,
    poll_interval: float = POLL_INTERVAL,
) -> tuple[str, dict, bool, PermissionRequest]:
    """Aplica rewrites, crea+publica el PermissionRequest, espera la respuesta y
    la aplica (idempotente). Devuelve (answer_final, input_efectivo, cambió, req).
    answer_final ∈ {allow, allow_always, deny, timeout}."""
    from asgiref.sync import sync_to_async

    from panel.core.services import rewrite

    if hooks is None:
        hooks = rewrite.get_hooks()
    effective, changed = rewrite.apply_rewrites(tool_name, input_data, hooks)
    timeout_s = await sync_to_async(timeout_seconds)(session)
    req = await sync_to_async(create_request)(session, tool_name, effective, timeout_s)
    await aredis.publish(bus.key_perm(str(session.id)), json.dumps(serialize_request(req)))
    raw = await _wait_answer(aredis, str(req.id), timeout_s, poll_interval)
    answer, source, option_index = _split_answer(raw)
    final = answer if answer in {"allow", "allow_always", "deny"} else "timeout"
    # SP9.1: si la SPA eligió una opción de AskUserQuestion, inyectamos
    # la respuesta en el input efectivo que verá el SDK como updated_input.
    if (
        tool_name == "AskUserQuestion"
        and option_index is not None
        and isinstance(effective, dict)
    ):
        effective = {**effective, "answer": int(option_index)}
        changed = True
    await sync_to_async(apply_answer)(req, final, source=source, always_rules=always_rules)
    # Notifica la resolución (cualquier origen) para que el tg_bridge edite el
    # mensaje de Telegram y quite el teclado (§4.6). Best-effort.
    payload = json.dumps({"request_id": str(req.id), "outcome": final, "source": source})
    try:
        # Global: tg_bridge edita el mensaje de Telegram.
        await aredis.publish(bus.key_perm_resolved(), payload)
    except Exception:  # noqa: BLE001
        pass
    # SP9.2: session-scoped, además. El WS del chat se suscribe aquí para
    # enterar a la SPA de la resolución (sea por web o por Telegram) y poder
    # actualizar el bubble (deshabilitar botones, mostrar "✓ Permitido").
    try:
        await aredis.publish(bus.key_perm_resolved_session(str(session.id)), payload)
    except Exception:  # noqa: BLE001
        pass
    return final, effective, changed, req
