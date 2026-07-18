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
    return {
        "id": str(req.id),
        "session": str(req.session_id),
        "tool": req.tool,
        "input_preview": req.input_preview,
        "status": req.status,
        "expires_at": req.expires_at.isoformat(),
    }


def apply_answer(
    req: PermissionRequest, answer: str, always_rules: list[str] | None = None
) -> None:
    """Transición idempotente del PermissionRequest según la respuesta ganadora.
    `allow_always` además persiste la(s) regla(s) en la policy y dispara re-render.
    `always_rules` viene de las suggestions del SDK (ya scopeadas, p.ej.
    `Bash(git push:*)`); si no hay, cae al nombre de la herramienta."""
    if req.status != PermissionRequest.Status.PENDING:
        return  # ya resuelto: idempotente
    mapping = {
        "allow": (PermissionRequest.Status.ALLOWED, PermissionRequest.ResolvedBy.WEB),
        "allow_always": (PermissionRequest.Status.ALLOWED_ALWAYS, PermissionRequest.ResolvedBy.WEB),
        "deny": (PermissionRequest.Status.DENIED, PermissionRequest.ResolvedBy.WEB),
        "timeout": (PermissionRequest.Status.EXPIRED, PermissionRequest.ResolvedBy.TIMEOUT),
    }
    status, resolved_by = mapping[answer]
    req.status = status
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


def claim_answer_sync(redis_client, request_id: str, answer: str) -> bool:
    """Escribe la respuesta con SET NX. True si este llamador la reclamó;
    False si otro ya había respondido (conflicto). Redis síncrono (web/HTTP)."""
    if answer not in {"allow", "deny", "allow_always"}:
        raise ValueError(f"respuesta inválida: {answer}")
    return bool(redis_client.set(bus.key_answer(request_id), answer, nx=True, ex=bus.ANSWER_TTL))


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
    answer = await _wait_answer(aredis, str(req.id), timeout_s, poll_interval)
    final = answer if answer in {"allow", "allow_always", "deny"} else "timeout"
    await sync_to_async(apply_answer)(req, final, always_rules)
    return final, effective, changed, req
