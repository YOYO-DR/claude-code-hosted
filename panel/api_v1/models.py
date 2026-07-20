"""API REST para ModelProfile (FASE D).

El endpoint expone CRUD con `auth_token` write-only: el POST/PATCH
aceptan el token en el body, pero NUNCA se devuelve (ni siquiera cifrado)
en GET. Mismo patrón que el PAT de GitHub.
"""

from __future__ import annotations

import json

from django.http import HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from panel.core.models import ModelProfile
from panel.core.services import models as model_svc

from .auth import require_verified_json


def _serialize(profile: ModelProfile) -> dict:
    return model_svc.serialize(profile)


def _parse_body(request: HttpRequest) -> dict:
    try:
        return json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return {}


@require_GET
@require_verified_json
def list_models(request: HttpRequest) -> JsonResponse:
    qs = ModelProfile.objects.order_by("name")
    return JsonResponse([_serialize(p) for p in qs], safe=False)


@csrf_exempt
@require_POST
@require_verified_json
def create_model(request: HttpRequest) -> JsonResponse:
    body = _parse_body(request)
    name = (body.get("name") or "").strip()
    provider = (body.get("provider") or "").strip()
    model_name = (body.get("model") or "").strip()
    base_url = (body.get("base_url") or "").strip() or None
    token = (body.get("auth_token") or "").strip()
    extra_env = body.get("extra_env") or {}

    if not name or not provider or not model_name:
        return JsonResponse(
            {"error": "name, provider y model son requeridos"},
            status=400,
        )
    if provider not in dict(ModelProfile.Provider.choices):
        return JsonResponse(
            {"error": f"provider inválido: {provider}"},
            status=400,
        )
    if ModelProfile.objects.filter(name=name).exists():
        return JsonResponse({"error": "name ya existe"}, status=409)

    profile = ModelProfile.objects.create(
        name=name,
        provider=provider,
        model=model_name,
        base_url=base_url,
        extra_env=extra_env,
    )
    if token:
        model_svc.store_token(profile, token)
        profile.save(update_fields=["auth_token_enc", "updated_at"])
    return JsonResponse(_serialize(profile), status=201)


@csrf_exempt
@require_http_methods(["PATCH"])
@require_verified_json
def update_model(request: HttpRequest, pk: int) -> JsonResponse:
    profile = get_object_or_404(ModelProfile, pk=pk)
    body = _parse_body(request)
    if "name" in body:
        profile.name = (body["name"] or "").strip() or profile.name
    if "provider" in body:
        new_provider = (body["provider"] or "").strip()
        if new_provider not in dict(ModelProfile.Provider.choices):
            return JsonResponse({"error": f"provider inválido: {new_provider}"}, status=400)
        profile.provider = new_provider
    if "model" in body:
        profile.model = (body["model"] or "").strip() or profile.model
    if "base_url" in body:
        profile.base_url = (body["base_url"] or "").strip() or None
    if "extra_env" in body:
        profile.extra_env = body["extra_env"] or {}
    if "auth_token" in body:
        # Si el cliente manda auth_token explícitamente, lo guardamos (puede
        # ser string vacío para BORRAR el token).
        token = (body["auth_token"] or "").strip()
        model_svc.store_token(profile, token)
    profile.save()
    return JsonResponse(_serialize(profile))


@csrf_exempt
@require_http_methods(["DELETE"])
@require_verified_json
def delete_model(request: HttpRequest, pk: int) -> JsonResponse:
    profile = get_object_or_404(ModelProfile, pk=pk)
    # Defensa: no permitir borrar un profile usado por proyectos.
    used = profile.projects.exists()
    if used:
        return JsonResponse(
            {"error": "profile en uso por uno o más proyectos; reasígnalos primero"},
            status=409,
        )
    profile.delete()
    return JsonResponse({"ok": True})


@csrf_exempt
@require_POST
@require_verified_json
def test_model(request: HttpRequest, pk: int) -> JsonResponse:
    """POST /api/v1/models/<pk>/test/ → ping al base_url. Devuelve
    {ok, status, model, provider} sin exponer el token."""
    profile = get_object_or_404(ModelProfile, pk=pk)
    return JsonResponse(model_svc.ping(profile))


@csrf_exempt
@require_POST
@require_verified_json
def set_project_model(request: HttpRequest, slug: str) -> JsonResponse:
    """POST /api/v1/projects/<slug>/model/ {model_profile_id} → cambia el
    model_profile del proyecto. Si el modelo difiere del actual, la sesión
    activa (si la hay) necesitará reinicio para tomar el nuevo modelo."""
    from panel.core.models import Project
    project = get_object_or_404(Project, slug=slug, status=Project.Status.ACTIVE)
    body = _parse_body(request)
    pk = body.get("model_profile_id")
    if pk is None:
        return JsonResponse({"error": "model_profile_id requerido"}, status=400)
    try:
        new_profile = ModelProfile.objects.get(pk=pk)
    except ModelProfile.DoesNotExist:
        return JsonResponse({"error": "model_profile no existe"}, status=404)
    old = project.model_profile
    project.model_profile = new_profile
    project.save(update_fields=["model_profile", "updated_at"])
    return JsonResponse({
        "ok": True,
        "old_model_profile": old.id,
        "new_model_profile": new_profile.id,
        "needs_restart": old.id != new_profile.id,
    })