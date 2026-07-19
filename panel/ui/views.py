from __future__ import annotations

import json

import redis
from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django_otp import login as otp_login
from django_otp.plugins.otp_totp.models import TOTPDevice

from panel.core.models import Config, PermissionRequest, Project, Session
from panel.core.services import github as gh
from panel.core.services import permissions as perm_svc
from panel.core.services import sessions as session_svc
from panel.core.services import telegram as tg
from panel.ui.forms import LoginForm


def login_view(request):
    if request.user.is_authenticated and request.user.is_verified():
        return redirect("session_list")
    form = LoginForm(request.POST or None)
    error = None
    if request.method == "POST" and form.is_valid():
        user = authenticate(
            request,
            username=form.cleaned_data["username"],
            password=form.cleaned_data["password"],
        )
        device = None
        if user is not None:
            device = TOTPDevice.objects.filter(user=user, confirmed=True).first()
        if (
            user is not None
            and device is not None
            and device.verify_token(form.cleaned_data["token"])
        ):
            login(request, user)
            otp_login(request, device)
            return redirect("session_list")
        error = "Credenciales o código TOTP inválidos."
    return render(request, "ui/login.html", {"form": form, "error": error})


def logout_view(request):
    logout(request)
    return redirect("login")


def _verified_required(request):
    return request.user.is_authenticated and request.user.is_verified()


@login_required
def session_list(request):
    if not request.user.is_verified():
        return redirect("login")
    sessions = Session.objects.select_related("project").order_by("-created_at")[:100]
    projects = Project.objects.filter(status=Project.Status.ACTIVE)
    return render(request, "ui/session_list.html", {"sessions": sessions, "projects": projects})


@login_required
def session_detail(request, sid):
    if not request.user.is_verified():
        return redirect("login")
    session = get_object_or_404(Session.objects.select_related("project__model_profile"), id=sid)
    events = session.events.order_by("seq")
    last_event = events.last()
    last_seq = last_event.seq if last_event is not None else 0
    return render(
        request,
        "ui/session_detail.html",
        {
            "session": session,
            "events": events,
            "last_seq": last_seq,
            "needs_restart": session_svc.needs_restart(session),
        },
    )


@login_required
@require_POST
def session_start(request, slug):
    if not request.user.is_verified():
        return HttpResponse(status=403)
    project = get_object_or_404(Project, slug=slug, status=Project.Status.ACTIVE)
    session = session_svc.start_session(project)
    return redirect("session_detail", sid=session.id)


@login_required
@require_POST
def session_stop(request, sid):
    if not request.user.is_verified():
        return HttpResponse(status=403)
    session = get_object_or_404(Session, id=sid)
    session_svc.stop_session(session)
    return redirect("session_detail", sid=session.id)


@login_required
def permission_queue(request):
    if not request.user.is_verified():
        return redirect("login")
    pending = (
        PermissionRequest.objects.filter(status=PermissionRequest.Status.PENDING)
        .select_related("session__project")
        .order_by("expires_at")
    )
    return render(request, "ui/permission_queue.html", {"pending": pending})


@login_required
@require_POST
def permission_resolve(request, request_id):
    """Reclama la respuesta vía SET NX (§4.1). El primero gana; si otro origen ya
    respondió, devuelve conflicto. El worker (único escritor) transiciona la fila."""
    if not request.user.is_verified():
        return JsonResponse({"error": "unauthorized"}, status=403)
    answer = request.POST.get("answer")
    if answer not in {"allow", "deny", "allow_always"}:
        return JsonResponse({"error": "invalid answer"}, status=400)
    client = redis.from_url(settings.REDIS_URL)
    try:
        claimed = perm_svc.claim_answer_sync(client, str(request_id), answer)
    except redis.RedisError:
        return JsonResponse({"error": "bus unavailable"}, status=503)
    finally:
        client.close()
    return JsonResponse({"ok": claimed, "conflict": not claimed})


@login_required
def github_settings(request):
    """Ajustes de GitHub: pegar el token (por frontend), validarlo (autentica +
    lista repos) y guardarlo cifrado en BD. El token nunca se re-muestra."""
    if not request.user.is_verified():
        return redirect("login")
    result = None
    if request.method == "POST":
        token = (request.POST.get("token") or "").strip()
        if token:
            result = gh.validate(token)
            if result["ok"]:
                gh.store_token(token)
        else:
            # revalidar el token ya guardado
            stored = gh.get_token()
            result = (
                gh.validate(stored)
                if stored
                else {"ok": False, "error": "no hay token guardado", "repos": []}
            )
    elif gh.has_token():
        stored = gh.get_token()
        result = gh.validate(stored) if stored else None
    return render(
        request,
        "ui/github.html",
        {"has_token": gh.has_token(), "result": result},
    )


@csrf_exempt
@require_POST
def tg_webhook(request):
    """Webhook de Telegram (§4.6). Valida el secret token, filtra por allowlist,
    procesa SOLO callback_query (los `message` sueltos se ignoran). Resuelve el
    permiso con SET NX y responde el callback (doble tap → 'ya respondida')."""
    secret = Config.get("tg_webhook_secret")
    got = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if not secret or got != secret:
        return HttpResponse(status=403)
    try:
        update = json.loads(request.body)
    except (json.JSONDecodeError, TypeError):
        return HttpResponse(status=200)  # nada que hacer, pero no reintentar

    cq = update.get("callback_query")
    if not cq:
        return HttpResponse(status=200)  # message suelto u otro update → ignorar

    from_id = (cq.get("from") or {}).get("id")
    if from_id not in settings.TELEGRAM_USER_IDS:
        return HttpResponse(status=200)  # fuera de allowlist → ignorar en silencio

    parsed = tg.parse_callback_data(cq.get("data", ""))
    if parsed is None:
        return HttpResponse(status=200)
    answer, request_id = parsed

    client = redis.from_url(settings.REDIS_URL)
    try:
        claimed = perm_svc.claim_answer_sync(client, request_id, answer, source="telegram")
    except (redis.RedisError, ValueError):
        claimed = False
    finally:
        client.close()

    try:
        if claimed:
            tg.answer_callback_query(cq["id"], f"Registrado: {answer}")
        else:
            tg.answer_callback_query(cq["id"], "Ya fue respondida.")
    except tg.TelegramError:
        pass
    return HttpResponse(status=200)
