"""SP15 — endpoints de la vista de contenedores Docker.

GET  /api/v1/docker/        → {groups: [...], standalone: [...]}
POST /api/v1/docker/stop/   → {container: "<id|name>"} o {project: "<compose>"}

Solo `stop`. Los contenedores de la infra del panel se filtran en el servicio y
el helper sudo los rechaza aunque llegue la orden.
"""

from __future__ import annotations

import json

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from panel.core.services import docker as docker_svc

from .auth import require_verified_json


@require_GET
@require_verified_json
def list_containers(request: HttpRequest) -> JsonResponse:
    try:
        return JsonResponse(docker_svc.list_containers())
    except docker_svc.DockerError as exc:
        # 127 = docker ausente: es un estado esperable (dev local sin docker),
        # no un 500. Devolvemos la lista vacía + el motivo para que la SPA
        # pinte un placeholder en vez de un error rojo.
        if exc.code == 127:
            return JsonResponse({
                "groups": [], "standalone": [], "unavailable": str(exc),
            })
        return JsonResponse({"error": str(exc)}, status=502)


@csrf_exempt
@require_POST
@require_verified_json
def stop(request: HttpRequest) -> JsonResponse:
    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "json inválido"}, status=400)
    container = (body.get("container") or "").strip()
    project = (body.get("project") or "").strip()
    if bool(container) == bool(project):
        return JsonResponse(
            {"error": "manda exactamente uno: 'container' o 'project'"}, status=400
        )
    try:
        if project:
            return JsonResponse(docker_svc.stop_project(project))
        return JsonResponse(docker_svc.stop_container(container))
    except docker_svc.DockerError as exc:
        status = {2: 400, 3: 403, 404: 404}.get(exc.code, 502)
        return JsonResponse({"error": str(exc)}, status=status)
