from __future__ import annotations

import json
import os

import redis
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django_otp import login as otp_login
from django_otp.plugins.otp_totp.models import TOTPDevice

from panel.core.models import Config, McpServer, Project, Session
from panel.core.services import github as gh
from panel.core.services import permissions as perm_svc
from panel.core.services import privileged
from panel.core.services import provisioning as prov_svc
from panel.core.services import sessions as session_svc
from panel.core.services import telegram as tg
from panel.ui.forms import LoginForm, McpServerForm, ProjectForm


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
    """Arranca una sesión. Si el path del proyecto no existe (D12 — caso típico:
    el clone inicial falló y la fila quedó con path inválido, o el operador
    borró el dir a mano), NO crea una sesión zombie: redirige a la lista con
    un mensaje claro."""
    if not request.user.is_verified():
        return HttpResponse(status=403)
    project = get_object_or_404(Project, slug=slug, status=Project.Status.ACTIVE)
    if not os.path.isdir(project.path):
        messages.error(
            request,
            f"No se puede arrancar la sesión: el path del proyecto "
            f"({project.path}) no existe. Probablemente el clone inicial "
            f"falló. Verifica el acceso al repo en /github/ y archiva "
            f"este proyecto para limpiarlo.",
        )
        return redirect("session_list")
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
def project_create(request):
    """Form de creación. Al guardar exitosamente, provisiona (clone/init +
    AGENTS.md) y redirige a la lista de sesiones.

    Si el provisioning falla con `ProvisioningError` (D12 — el caso típico es
    que el PAT de GitHub no tenga acceso al repo), hace rollback del proyecto
    y devuelve 400 con el mensaje legible. NO se devuelve 502 (eso era un bug:
    el operador no podía distinguir "falló el clone" de "panel caído").
    """
    if not request.user.is_verified():
        return redirect("login")
    if request.method == "POST":
        form = ProjectForm(request.POST)
        if form.is_valid():
            project = form.save()
            try:
                prov_svc.provision_project(project)
            except privileged.ProvisioningError as exc:
                # Rollback: borramos la fila a medias + intentamos limpiar
                # cualquier residuo en /srv/projects (idempotente).
                Project.objects.filter(pk=project.pk).delete()
                _safe_cleanup_failed_clone(project.path)
                return render(
                    request,
                    "ui/project_form.html",
                    {"form": form, "error": str(exc)},
                    status=400,
                )
            except Exception as exc:  # noqa: BLE001 — defensa de último recurso
                Project.objects.filter(pk=project.pk).delete()
                _safe_cleanup_failed_clone(project.path)
                return render(
                    request,
                    "ui/project_form.html",
                    {"form": form, "error": f"provisioning falló: {exc}"},
                    status=400,
                )
            return redirect("session_list")
    else:
        form = ProjectForm()
    return render(request, "ui/project_form.html", {"form": form})


def _safe_cleanup_failed_clone(path: str) -> None:
    """Borra el dir del proyecto si existe y quedó vacío/con contenido parcial.
    Se ejecuta como `panel` (sin root); si el helper de sudo lo creó con
    permisos de root no podemos borrarlo desde aquí — en ese caso dejamos que
    el operador lo limpie con sudo desde admin. No falla."""
    import shutil

    try:
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
    except Exception:  # noqa: BLE001 — best-effort
        pass


@login_required
def project_archive(request, slug):
    """Archivar un proyecto (POST). Mantiene datos y archivos; detiene sesiones."""
    if not request.user.is_verified():
        return HttpResponse(status=403)
    if request.method != "POST":
        return redirect("session_list")
    project = get_object_or_404(Project, slug=slug)
    prov_svc.archive_project(project)
    return redirect("session_list")


@login_required
def mcp_list(request):
    """Listado de MCPs (global + project) para gestionar y crear nuevos."""
    if not request.user.is_verified():
        return redirect("login")
    mcps = McpServer.objects.select_related("project").order_by("scope", "name")
    return render(request, "ui/mcp_list.html", {"mcps": mcps})


@login_required
def mcp_create(request):
    """Form de creación. Al guardar, dispara render (re-genera .mcp.json)."""
    if not request.user.is_verified():
        return redirect("login")
    if request.method == "POST":
        form = McpServerForm(request.POST)
        if form.is_valid():
            form.save()
            try:
                privileged.run_render()
            except Exception as exc:
                return render(
                    request,
                    "ui/mcp_form.html",
                    {"form": form, "error": f"guardado OK pero render falló: {exc}"},
                    status=502,
                )
            return redirect("mcp_list")
    else:
        form = McpServerForm()
    return render(request, "ui/mcp_form.html", {"form": form})


@login_required
def mcp_edit(request, mcp_id):
    if not request.user.is_verified():
        return HttpResponse(status=403)
    mcp = get_object_or_404(McpServer, id=mcp_id)
    if request.method == "POST":
        form = McpServerForm(request.POST, instance=mcp)
        if form.is_valid():
            form.save()
            try:
                privileged.run_render()
            except Exception as exc:
                return render(
                    request,
                    "ui/mcp_form.html",
                    {"form": form, "mcp": mcp, "error": f"guardado OK pero render falló: {exc}"},
                    status=502,
                )
            return redirect("mcp_list")
    else:
        form = McpServerForm(instance=mcp)
    return render(request, "ui/mcp_form.html", {"form": form, "mcp": mcp})


@login_required
@require_POST
def mcp_toggle(request, mcp_id):
    """Enable/disable rápido. POST. Regenera render."""
    if not request.user.is_verified():
        return HttpResponse(status=403)
    mcp = get_object_or_404(McpServer, id=mcp_id)
    mcp.enabled = not mcp.enabled
    mcp.save(update_fields=["enabled", "updated_at"])
    privileged.run_render()
    return redirect("mcp_list")


@login_required
def permission_queue(request):
    if not request.user.is_verified():
        return redirect("login")
    # D11: query única filtrada por sesión viva + expires_at — vista y badge
    # usan `perm_svc.live_pending_qs()` para no divergir.
    pending = (
        perm_svc.live_pending_qs()
        .select_related("session__project")
        .order_by("expires_at")
    )
    return render(request, "ui/permission_queue.html", {"pending": pending})


@login_required
@require_POST
def permission_resolve(request, request_id):
    """Resuelve la PermissionRequest de forma transaccional (D11 / MIGRATION1 §2.2):

    1. `resolve_atomically` hace el UPDATE … WHERE id=? AND status='pending'
       dentro de transaction.atomic con SELECT FOR UPDATE SKIP LOCKED — gana
       el primero, el resto recibe `conflict=true`.
    2. Solo si ganó (claimed=True) publica la respuesta en Redis (`SET NX`)
       para que el worker despierte y siga el flujo del SDK.
    3. Si la sesión ya no está viva, la request queda `expired` (cleanup) y
       el caller recibe `conflict=true` (sin SET NX, sin ejecutar tool).
    """
    if not request.user.is_verified():
        return JsonResponse({"error": "unauthorized"}, status=403)
    answer = request.POST.get("answer")
    if answer not in {"allow", "deny", "allow_always"}:
        return JsonResponse({"error": "invalid answer"}, status=400)
    try:
        claimed, req = perm_svc.resolve_atomically(
            str(request_id), answer, source="web"
        )
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    if not claimed:
        return JsonResponse({"ok": False, "conflict": True})
    # Solo si ganamos en DB notificamos al worker (idempotente para el worker).
    client = redis.from_url(settings.REDIS_URL)
    try:
        ok = perm_svc.claim_answer_sync(client, str(request_id), answer, source="web")
    except redis.RedisError:
        # La fila ya está marcada; el worker la verá expirada cuando despierte.
        return JsonResponse({"ok": True, "warn": "bus unavailable, fila persistida"})
    finally:
        client.close()
    # allow_always: persistir reglas (best-effort, no afecta respuesta).
    if answer == "allow_always" and req is not None:
        perm_svc._persist_always_rules(req, [req.tool])  # noqa: SLF001
    return JsonResponse({"ok": ok, "conflict": not ok})


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
