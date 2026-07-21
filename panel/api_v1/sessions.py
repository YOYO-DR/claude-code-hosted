"""Sesiones: list, detail, message, stop, events (backlog con UIEvent)."""

from __future__ import annotations

import json
import os

from django.http import HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST, require_http_methods

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
    """GET /api/v1/sessions/?status=&project=&q=&limit=&offset=
    Lista de sesiones recientes (default 200). Soporta filtros:
      - status: CSV de status (ej 'running,waiting_approval')
      - project: slug exacto (o 'null' para sesiones huérfanas)
      - q: texto libre — busca en project.slug y session.id prefix
      - limit (default 200, máx 500), offset (default 0)
    Orden: -created_at.
    """
    qs = Session.objects.select_related("project").order_by("-created_at")

    # status filter (CSV)
    status_csv = (request.GET.get("status") or "").strip()
    if status_csv:
        wanted = [s.strip() for s in status_csv.split(",") if s.strip()]
        valid = {c[0] for c in Session.Status.choices}
        wanted = [s for s in wanted if s in valid]
        if wanted:
            qs = qs.filter(status__in=wanted)

    # project slug filter
    proj = (request.GET.get("project") or "").strip()
    if proj == "null":
        qs = qs.filter(project__isnull=True)
    elif proj:
        qs = qs.filter(project__slug=proj)

    # texto libre en slug o prefijo UUID
    q = (request.GET.get("q") or "").strip()
    if q:
        from django.db.models import Q
        qs = qs.filter(Q(project__slug__icontains=q) | Q(id__istartswith=q))

    # paging
    try:
        limit = min(max(int(request.GET.get("limit", "50")), 1), 200)
    except ValueError:
        limit = 50
    try:
        page_1 = max(int(request.GET.get("page", "1")), 1)
    except ValueError:
        page_1 = 1

    total = qs.count()
    pages = max(1, (total + limit - 1) // limit)
    if page_1 > pages:
        page_1 = pages
    offset = (page_1 - 1) * limit

    page = qs[offset:offset + limit]
    return JsonResponse({
        "total": total,
        "limit": limit,
        "page": page_1,
        "pages": pages,
        "results": [_serialize_session(s) for s in page],
    }, safe=False)


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

# ---- UX-T.6: session create from project slug ----

@csrf_exempt
@require_http_methods(["POST"])
@require_verified_json
def session_create(request: HttpRequest) -> JsonResponse:
    """POST /api/v1/sessions/create/ body={"slug": "<slug>"}
    Crea una nueva Session para el proyecto y arranca el worker. Devuelve
    {ok, id, status} con la sesión creada.
    409 si el proyecto tiene una sesión activa (running/idle/waiting_approval).
    400 si slug falta o el proyecto no existe / path inválido.
    502 si systemctl falla al arrancar el worker (con rollback de la fila
    para no dejar zombies en STARTING — SP2).
    """
    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "json inválido"}, status=400)
    if not isinstance(body, dict):
        return JsonResponse({"error": "body debe ser objeto JSON"}, status=400)
    slug = (body.get("slug") or "").strip()
    if not slug:
        return JsonResponse({"error": "slug requerido"}, status=400)
    from panel.core.models import Project
    project = get_object_or_404(Project, slug=slug, status=Project.Status.ACTIVE)
    if not os.path.isdir(project.path):
        return JsonResponse(
            {"error": f"path del proyecto inexistente: {project.path}. Probablemente el clone falló."},
            status=400,
        )
    # Si ya hay una sesión activa, devolvemos 409 y la existing.
    from panel.core.models import Session
    existing = project.sessions.filter(
        status__in=(Session.Status.RUNNING, Session.Status.IDLE, Session.Status.WAITING_APPROVAL)
    ).order_by("-created_at").first()
    if existing:
        return JsonResponse(
            {"ok": True, "id": str(existing.id), "status": existing.status, "reused": True},
            status=409,
        )
    # SP2: una sola fila. `start_session` la crea Y arranca el worker.
    # Si el arranque falla, start_session hace rollback y propaga
    # SupervisorError → devolvemos 502 con el stderr del systemctl.
    from panel.core.services import sessions as session_svc
    from workers import supervisor
    try:
        s = session_svc.start_session(project)
    except supervisor.SupervisorError as exc:
        return JsonResponse(
            {"error": f"no pude arrancar el worker: {exc}. Vuelve a intentarlo; si persiste, revisa los sudoers del panel."},
            status=502,
        )
    return JsonResponse({"ok": True, "id": str(s.id), "status": s.status}, status=201)
