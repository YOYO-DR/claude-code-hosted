"""Env del modelo para ClaudeAgentOptions.env (§4.2/§4.3). El token se descifra
de la DB en memoria del worker y se pasa por env; NUNCA se escribe a disco."""

from __future__ import annotations

from panel.core.crypto import decrypt
from panel.core.models import ModelProfile


def render_env(profile: ModelProfile) -> dict[str, str]:
    env: dict[str, str] = {}
    if profile.base_url:
        env["ANTHROPIC_BASE_URL"] = profile.base_url
    if profile.auth_token_enc:
        env["ANTHROPIC_AUTH_TOKEN"] = decrypt(profile.auth_token_enc)
    for k, v in (profile.extra_env or {}).items():
        env[str(k)] = str(v)
    return env
