from __future__ import annotations

from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django_otp import login as otp_login
from django_otp.plugins.otp_totp.models import TOTPDevice

from panel.core.models import Project, Session
from panel.core.services import sessions as session_svc
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
    session = get_object_or_404(Session, id=sid)
    events = session.events.order_by("seq")
    last_seq = events.last().seq if events.exists() else 0
    return render(
        request,
        "ui/session_detail.html",
        {"session": session, "events": events, "last_seq": last_seq},
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
