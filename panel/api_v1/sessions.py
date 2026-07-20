"""Sesiones: list, detail, message, stop, events (backlog con UIEvent)."""

from __future__ import annotations

from django.http import HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from panel.core import bus
from panel.core.models import Session
from panel.core.services import sessions as session_svc

from .auth import require_verified_json


def _serialize_session(s: Session) -> dict:
    return {
        "id": str(s.id),
        "project": s.project.slug,
        "project_slug": s.project.slug,
        "status": s.status,
        "started_at": s.started_at.isoformat() if s.started_at else None,
        "ended_at": s.ended_at.isoformat() if s.ended_at else None,
        "total_cost_usd": float(s.total_cost_usd or 0),
        "model_reported": s.model_reported,
        "github_warn_no_push": s.project.github_warn_no_push,
    }


@require_GET
@require_verified_json
def list_sessions(request: HttpRequest) -> JsonResponse:
    qs = Session.objects.select_related("project").order_by("-created_at")[:200]
    return JsonResponse([_serialize_session(s) for s in qs], safe=False)


@require_GET
@require_verified_json
def session_detail(request: HttpRequest, sid: str) -> JsonResponse:
    s = get_object_or_404(Session, id=sid)
    return JsonResponse(_serialize_session(s))


@csrf_exempt
@require_POST
@require_verified_json
def session_message(request: HttpRequest, sid: str) -> JsonResponse:
    """Manda un mensaje del usuario al worker por Redis :in."""
    import json as _json

    import redis
    from django.conf import settings

    s = get_object_or_404(Session, id=sid)
    try:
        body = _json.loads(request.body or b"{}")
    except _json.JSONDecodeError:
        return JsonResponse({"error": "json inválido"}, status=400)
    text = (body.get("text") or "").strip()
    if not text:
        return JsonResponse({"error": "text requerido"}, status=400)
    if s.status not in (Session.Status.RUNNING, Session.Status.IDLE,
                         Session.Status.WAITING_APPROVAL):
        return JsonResponse({"error": f"sesión en estado {s.status}"}, status=409)
    client = redis.from_url(settings.REDIS_URL)
    try:
        client.lpush(bus.key_in(str(s.id)), _json.dumps({"type": "user_message", "text": text}))
    finally:
        client.close()
    return JsonResponse({"ok": True})


@csrf_exempt
@require_POST
@require_verified_json
def session_stop(request: HttpRequest, sid: str) -> JsonResponse:
    s = get_object_or_404(Session, id=sid)
    session_svc.stop_session(s)
    return JsonResponse({"ok": True, "status": s.status})


@require_GET
@require_verified_json
def session_events(request: HttpRequest, sid: str) -> JsonResponse:
    """Backlog desde `?since=` (default 0). Devuelve eventos crudos con
    `ui_event` poblado (FASE B). El cliente WS es la fuente en vivo; este
    endpoint es para el cold-start (cargar la historia)."""
    from panel.core.models import Event
    since = int(request.GET.get("since", "0") or 0)
    limit = min(int(request.GET.get("limit", "500") or 500), 2000)
    qs = (
        Event.objects.filter(session_id=sid, seq__gt=since)
        .order_by("seq")[:limit]
    )
    return JsonResponse([
        {
            "seq": e.seq,
            "type": e.type,
            "payload": e.payload,
            "ui_event": e.ui_event,
            "ts": e.ts.isoformat(),
        }
        for e in qs
    ], safe=False)