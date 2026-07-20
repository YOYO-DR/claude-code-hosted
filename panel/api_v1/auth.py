"""Auth JSON para el SPA: /api/v1/login/, /api/v1/logout/, /api/v1/me/."""

from __future__ import annotations

import json

from django.contrib.auth import authenticate, login, logout
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST
from django_otp import login as otp_login
from django_otp.plugins.otp_totp.models import TOTPDevice


def _is_verified(request) -> bool:
    """Lee `is_verified` del usuario de la request.

    django_otp.is_verified() evalúa contra la sesión actual. Cuando
    `otp_login()` se acaba de llamar dentro del MISMO request, el atributo
    del modelo User (que es plano) sigue False — el OTPMiddleware aún no
    ha tenido oportunidad de reasignar el user. Pero la sesión YA tiene
    `otp_device_id` seteada (django_otp.login escribe en la sesión
    inmediatamente). La forma robusta: leer de la sesión.
    """
    # 1) Sesión tiene el device verificado (caso normal tras otp_login).
    # django_otp usa 'otp_device_id' como clave (DEVICE_ID_SESSION_KEY).
    if request.session.get("otp_device_id"):
        return True
    # 2) Fallback: invocar user.is_verified() por si el middleware ya marcó.
    user = getattr(request, "user", None)
    if user is None:
        return False
    val = getattr(user, "is_verified", None)
    if val is None:
        return False
    if callable(val):
        try:
            return bool(val())
        except (TypeError, Exception):  # noqa: BLE001
            return False
    return bool(val)


def _user_payload(request) -> dict:
    """Devuelve el payload JSON del usuario actual. Usa request (no user)
    porque necesitamos leer de la sesión para detectar verificación
    post-otp_login en el mismo request (ver _is_verified)."""
    user = request.user
    return {
        "id": user.id,
        "username": user.username,
        "is_verified": _is_verified(request),
    }


# Decorador de conveniencia para vistas api_v1 que requieren TOTP verificado.
def require_verified_json(view_func):
    """Decorador: rechaza con 403 JSON si el user no está autenticado o no
    tiene TOTP verificado. Uso:
        @require_GET
        @require_verified_json
        def my_view(request):
            ...
    """
    import functools

    from django.http import JsonResponse

    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({"detail": "unauthenticated"}, status=401)
        if not _is_verified(request):
            return JsonResponse({"detail": "TOTP no verificado"}, status=403)
        return view_func(request, *args, **kwargs)

    return wrapper


@require_GET
@ensure_csrf_cookie
@require_verified_json
def me(request: HttpRequest) -> JsonResponse:
    """Devuelve el usuario actual o 401 si no hay sesión válida.
    Importante: poner la cookie csrftoken para que el SPA pueda hacer POST."""
    return JsonResponse(_user_payload(request))


@csrf_exempt
@require_POST
def login_view(request: HttpRequest) -> JsonResponse:
    """POST {username, password, otp_token} → JSON {ok, user, next}.

    Replica el flujo de Django (authenticate + login + otp_login) pero
    devuelve JSON en vez de redirigir. La cookie de sesión la pone Django
    automáticamente al hacer login() — el SPA la hereda con credentials.
    """
    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "json inválido"}, status=400)

    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    otp_token = (body.get("otp_token") or "").strip()
    next_url = body.get("next") or "/sessions"

    if not username or not password:
        return JsonResponse({"error": "usuario y contraseña requeridos"}, status=400)

    user = authenticate(request, username=username, password=password)
    if user is None:
        return JsonResponse({"ok": False, "error": "credenciales inválidas"}, status=401)
    if not user.is_active:
        return JsonResponse({"ok": False, "error": "usuario inactivo"}, status=403)

    login(request, user)

    if otp_token:
        device = TOTPDevice.objects.filter(user=user, confirmed=True).first()
        if device is None:
            return JsonResponse({"ok": False, "error": "no hay TOTP configurado"}, status=400)
        if not device.verify_token(otp_token):
            return JsonResponse({"ok": False, "error": "código TOTP inválido"}, status=401)
        otp_login(request, device)

    # Tras otp_login(), la sesión SÍ está verificada — leemos de request.user
    # (que es lo que mira el OTPMiddleware). El user original tenía is_verified
    # a False (django_otp solo inyecta is_verified() tras verificar el device
    # contra la sesión actual; el atributo del modelo User nunca cambia).
    return JsonResponse(
        {"ok": True, "user": _user_payload(request), "next": next_url}
    )


@csrf_exempt
@require_POST
def logout_view(request: HttpRequest) -> JsonResponse:
    logout(request)
    return JsonResponse({"ok": True})