"""GitHub settings JSON (FASE C.3). POST {token?} valida y guarda cifrado."""

from __future__ import annotations

import json

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from panel.core.services import github as gh_svc

from .auth import require_verified_json


@require_GET
@require_verified_json
def github_info(request: HttpRequest) -> JsonResponse:
    """Devuelve has_token + resultado de validar el token guardado."""
    has = gh_svc.has_token()
    if not has:
        return JsonResponse({"has_token": False, "result": None})
    token = gh_svc.get_token()
    return JsonResponse({
        "has_token": True,
        "result": gh_svc.validate(token) if token else None,
    })


@csrf_exempt
@require_POST
@require_verified_json
def github_store(request: HttpRequest) -> JsonResponse:
    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "json inválido"}, status=400)
    token = (body.get("token") or "").strip()
    if not token:
        # revalidar token guardado
        if gh_svc.has_token():
            stored = gh_svc.get_token()
            return JsonResponse({
                "result": gh_svc.validate(stored) if stored else None,
            })
        return JsonResponse({"error": "no hay token"}, status=400)
    result = gh_svc.validate(token)
    if not result.get("ok"):
        return JsonResponse({"error": "token inválido", "result": result}, status=400)
    gh_svc.store_token(token)
    return JsonResponse({"ok": True, "result": result})