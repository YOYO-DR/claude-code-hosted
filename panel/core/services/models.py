"""Servicio de ModelProfile (FASE D).

Mismo patrón que el servicio de GitHub:
- `auth_token_enc` se cifra con Fernet (MultiFernet).
- `auth_token_enc` NUNCA sale del backend. Se omite explícitamente del
  payload JSON que va al SPA.
- "Probar" hace un ping mínimo al `base_url` con el token en el header
  Authorization: Bearer; reporta ok/fallo sin exponer el token.
"""

from __future__ import annotations

import httpx

from panel.core import crypto
from panel.core.models import ModelProfile


def get_token(profile: ModelProfile) -> str | None:
    if not profile.auth_token_enc:
        return None
    try:
        # crypto.decrypt() ya retorna str (hace .decode() internamente);
        # no aplicar建模es otro .decode() aquí.
        return crypto.decrypt(profile.auth_token_enc)
    except Exception:  # noqa: BLE001
        return None


def store_token(profile: ModelProfile, token: str) -> None:
    """Cifra y guarda el token del profile (write-only en el API)."""
    if not token:
        profile.auth_token_enc = None
    else:
        profile.auth_token_enc = crypto.encrypt(token.strip())


def has_token(profile: ModelProfile) -> bool:
    return bool(profile.auth_token_enc)


def serialize(profile: ModelProfile) -> dict:
    """Serializa un profile para el SPA. NUNCA incluye `auth_token_enc`
    (ni siquiera cifrado) — es write-only."""
    return {
        "id": profile.id,
        "name": profile.name,
        "provider": profile.provider,
        "base_url": profile.base_url,
        "model": profile.model,
        "extra_env": profile.extra_env,
        "has_token": has_token(profile),
        "updated_at": profile.updated_at.isoformat(),
    }


def ping(profile: ModelProfile, *, timeout: float = 5.0) -> dict:
    """Hace un ping mínimo al `base_url` con el token. No expone el token
    en el resultado — solo ok/error y (si ok) un eco de `model` y `provider`."""
    token = get_token(profile)
    if not token:
        return {"ok": False, "error": "no hay token guardado"}
    url = (profile.base_url or "").rstrip("/")
    if not url:
        return {"ok": False, "error": "base_url vacío"}
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "claude-code-hosted-panel",
    }
    try:
        r = httpx.get(url, headers=headers, timeout=timeout)
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"error de red: {exc}"}
    if r.status_code in (200, 201, 204, 400, 401, 403, 404):
        # 2xx = OK (algunos providers responden 200/204, otros 401 que
        # significa "endpoint alcanzable, auth requerida" = token funciona
        # a nivel de transporte). 4xx = alcanzable pero el token no
        # autoriza — distinguible por la respuesta del provider.
        return {
            "ok": 200 <= r.status_code < 300,
            "status": r.status_code,
            "model": profile.model,
            "provider": profile.provider,
        }
    return {
        "ok": False,
        "status": r.status_code,
        "error": (r.text or "")[:200],
    }