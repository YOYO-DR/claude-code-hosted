"""Ciclo de vida de sesiones desde el panel: crear la fila, arrancar el worker,
pararlo. El worker real corre en su propia unidad systemd (claude-session@)."""

from __future__ import annotations

import json

import redis
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from panel.core import bus
from panel.core.models import McpServer, Project, Session
from workers import supervisor


def _redis() -> redis.Redis:
    return redis.from_url(settings.REDIS_URL)


def start_session(project: Project) -> Session:
    session = Session.objects.create(project=project, status=Session.Status.STARTING)
    supervisor.start(str(session.id))
    return session


def stop_session(session: Session) -> None:
    # Pedido de cierre limpio por el bus; luego se detiene la unidad.
    try:
        _redis().lpush(bus.key_in(str(session.id)), json.dumps({"type": "shutdown"}))
    except redis.RedisError:
        pass
    supervisor.stop(str(session.id))
    session.status = Session.Status.STOPPED
    session.ended_at = timezone.now()
    session.save(update_fields=["status", "ended_at", "updated_at"])


def needs_restart(session: Session) -> bool:
    """True si la config de MCP o el perfil de modelo del proyecto cambió
    después de arrancar la sesión (§4.3: los MCP no recargan en caliente).
    Usa updated_at (auto_now) — cero campos/migraciones nuevas."""
    if session.started_at is None:
        return False
    project = session.project
    if project.model_profile.updated_at > session.started_at:
        return True
    return McpServer.objects.filter(
        Q(scope=McpServer.Scope.GLOBAL) | Q(scope=McpServer.Scope.PROJECT, project=project),
        updated_at__gt=session.started_at,
    ).exists()
