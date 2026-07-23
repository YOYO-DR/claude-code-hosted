"""Permissions: cola y resolve (FASE C.3)."""

from __future__ import annotations

import json

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from panel.core.models import PermissionRequest
from panel.core.services import permissions as perm_svc

from .auth import require_verified_json


def _serialize(r: PermissionRequest) -> dict:
    return {
        "id": str(r.id),
        "session": str(r.session_id),
        "tool": r.tool,
        "input_preview": r.input_preview,
        "status": r.status,
        "resolved_by": r.resolved_by,
        "expires_at": r.expires_at.isoformat(),
        "session_status": r.session.status,
        "project_slug": r.session.project.slug,
    }


@require_GET
@require_verified_json
def list_permissions(request: HttpRequest) -> JsonResponse:
    """Cola filtrada por sesión viva + expires_at (D11)."""
    pending = perm_svc.live_pending_qs().select_related("session__project").order_by("expires_at")
    return JsonResponse([_serialize(r) for r in pending], safe=False)


@csrf_exempt
@require_POST
@require_verified_json
def resolve_permission(request: HttpRequest, perm_id: str) -> JsonResponse:
    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "json inválido"}, status=400)
    answer = body.get("answer")
    if answer not in {"allow", "deny", "allow_always"}:
        return JsonResponse({"error": "answer inválido"}, status=400)
    option_index = body.get("option_index")
    if option_index is not None:
        if not isinstance(option_index, int) or option_index < 0:
            return JsonResponse({"error": "option_index debe ser int >= 0"}, status=400)
    # SP14: AskUserQuestion multi-pregunta / multiSelect. El cliente manda
    # SOLO índices; los labels los resuelve el worker contra el input_full
    # guardado en la BD (el cliente nunca inyecta el texto de la respuesta).
    selections = body.get("selections")
    if selections is not None:
        if not isinstance(selections, dict):
            return JsonResponse({"error": "selections debe ser objeto"}, status=400)
        clean: dict[str, list[int]] = {}
        for k, v in selections.items():
            try:
                qi = int(k)
            except (TypeError, ValueError):
                return JsonResponse({"error": f"clave de pregunta inválida: {k!r}"}, status=400)
            if qi < 0:
                return JsonResponse({"error": "índice de pregunta negativo"}, status=400)
            idxs = [v] if isinstance(v, int) else v
            if not isinstance(idxs, list) or not all(
                isinstance(i, int) and i >= 0 for i in idxs
            ):
                return JsonResponse(
                    {"error": f"selecciones inválidas para la pregunta {qi}"}, status=400
                )
            clean[str(qi)] = idxs
        selections = clean
    try:
        claimed, req = perm_svc.resolve_atomically(perm_id, answer, source="web")
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    if not claimed:
        return JsonResponse({"ok": False, "conflict": True}, status=409)
    import redis
    from django.conf import settings

    client = redis.from_url(settings.REDIS_URL)
    try:
        perm_svc.claim_answer_sync(
            client, perm_id, answer, source="web",
            option_index=option_index, selections=selections,
        )
    finally:
        client.close()
    return JsonResponse({"ok": True, "status": req.status if req else None})