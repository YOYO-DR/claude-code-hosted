"""MCPs CRUD JSON (FASE C.3 + UX-T.3). Reutiliza el modelo McpServer."""

from __future__ import annotations

import json

from django.http import HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from panel.core.models import McpServer, Project
from panel.core.services import privileged

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


def _validate_payload(body: dict, *, is_create: bool) -> tuple[dict, str | None]:
    """Valida campos del body. Devuelve (cleaned_dict, error_msg)."""
    if not isinstance(body, dict):
        return {}, "body debe ser objeto JSON"
    if is_create and not body.get("name"):
        return {}, "campo `name` requerido"
    name = body.get("name")
    if name is not None and (not isinstance(name, str) or len(name) > 100):
        return {}, "`name` debe ser string de <= 100 chars"
    scope = body.get("scope", McpServer.Scope.GLOBAL)
    if scope not in (McpServer.Scope.GLOBAL, McpServer.Scope.PROJECT):
        return {}, f"`scope` debe ser 'global' o 'project'"
    transport = body.get("transport", McpServer.Transport.STDIO)
    if transport not in (McpServer.Transport.STDIO, McpServer.Transport.HTTP):
        return {}, f"`transport` debe ser 'stdio' o 'http'"
    project = body.get("project")
    if scope == McpServer.Scope.PROJECT and not project:
        return {}, "scope=project requiere `project` (slug)"
    enabled = bool(body.get("enabled", True))
    config = body.get("config", {})
    if config is None or not isinstance(config, dict):
        return {}, "`config` debe ser objeto JSON"
    cleaned = {
        "name": name,
        "scope": scope,
        "transport": transport,
        "project": project,
        "enabled": enabled,
        "config": config,
    }
    return cleaned, None


@csrf_exempt
@require_http_methods(["POST"])
@require_verified_json
def create_mcp(request: HttpRequest) -> JsonResponse:
    """POST /api/v1/mcps/create/ body={name, scope, project?, transport, config}"""
    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "json inválido"}, status=400)
    cleaned, err = _validate_payload(body, is_create=True)
    if err:
        return JsonResponse({"error": err}, status=400)
    proj = None
    if cleaned["project"]:
        try:
            proj = Project.objects.get(slug=cleaned["project"], status=Project.Status.ACTIVE)
        except Project.DoesNotExist:
            return JsonResponse({"error": f"project '{cleaned['project']}' no existe"}, status=400)
    if McpServer.objects.filter(name=cleaned["name"], scope=cleaned["scope"], project=proj).exists():
        return JsonResponse({"error": "ya existe un MCP con ese name+scope+project"}, status=409)
    m = McpServer.objects.create(
        name=cleaned["name"],
        scope=cleaned["scope"],
        transport=cleaned["transport"],
        project=proj,
        enabled=cleaned["enabled"],
        config=cleaned["config"],
    )
    try:
        privileged.run_render()
    except Exception:
        pass
    return JsonResponse(_serialize(m), status=201)


@csrf_exempt
@require_http_methods(["PATCH"])
@require_verified_json
def update_mcp(request: HttpRequest, mcp_id: int) -> JsonResponse:
    """PATCH /api/v1/mcps/<id>/update/ body parcial."""
    m = get_object_or_404(McpServer, id=mcp_id)
    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "json inválido"}, status=400)
    cleaned, err = _validate_payload(body, is_create=False)
    if err:
        return JsonResponse({"error": err}, status=400)
    if "name" in cleaned and cleaned["name"]:
        m.name = cleaned["name"]
    m.scope = cleaned["scope"]
    m.transport = cleaned["transport"]
    if "project" in cleaned:
        if cleaned["project"]:
            try:
                m.project = Project.objects.get(slug=cleaned["project"], status=Project.Status.ACTIVE)
            except Project.DoesNotExist:
                return JsonResponse({"error": f"project '{cleaned['project']}' no existe"}, status=400)
        else:
            m.project = None
    m.enabled = cleaned["enabled"]
    if "config" in cleaned:
        m.config = cleaned["config"]
    m.save()
    try:
        privileged.run_render()
    except Exception:
        pass
    return JsonResponse(_serialize(m))


@csrf_exempt
@require_http_methods(["DELETE"])
@require_verified_json
def delete_mcp(request: HttpRequest, mcp_id: int) -> JsonResponse:
    """DELETE /api/v1/mcps/<id>/delete/ — soft (enabled=false). ?hard=1 → delete físico."""
    m = get_object_or_404(McpServer, id=mcp_id)
    if request.GET.get("hard") == "1":
        m.delete()
        try:
            privileged.run_render()
        except Exception:
            pass
        return JsonResponse({"ok": True, "deleted": True})
    m.enabled = False
    m.save(update_fields=["enabled", "updated_at"])
    try:
        privileged.run_render()
    except Exception:
        pass
    return JsonResponse({"ok": True, "id": m.id, "enabled": False})
