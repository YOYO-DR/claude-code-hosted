"""MCPs CRUD JSON (FASE C.3). Reutiliza el modelo McpServer."""

from __future__ import annotations

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET

from panel.core.models import McpServer

from .auth import require_verified_json


def _serialize(m: McpServer) -> dict:
    return {
        "id": m.id,
        "name": m.name,
        "scope": m.scope,
        "project": m.project.slug if m.project else None,
        "transport": m.transport,
        "config": m.config,
        "enabled": m.enabled,
        "updated_at": m.updated_at.isoformat(),
    }


@require_GET
@require_verified_json
def list_mcps(request: HttpRequest) -> JsonResponse:
    qs = McpServer.objects.select_related("project").order_by("scope", "name")
    return JsonResponse([_serialize(m) for m in qs], safe=False)