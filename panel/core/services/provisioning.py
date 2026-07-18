"""Ciclo de vida de proyectos (Fase 2 §5). Crear un proyecto materializa su
directorio (`git init`, chown a `agents`) y re-renderiza TODOS los activos (las
deny obligatorias dinámicas de cada proyecto dependen de la lista completa).
Archivar detiene sus sesiones pero no borra datos ni archivos.

Las operaciones privilegiadas (chown, leer panel.env) se delegan en helpers vía
sudo — ver panel.core.services.privileged."""

from __future__ import annotations

import logging

from django.conf import settings

from panel.core.models import Config, Project, Session
from panel.core.services import privileged
from panel.core.services import sessions as session_svc
from panel.core.services import telegram as tg

log = logging.getLogger("provisioning")


def provision_project(project: Project) -> None:
    """Directorio + git init + chown agents + render de todos. Idempotente.
    Además crea el topic de Telegram del proyecto (§4.6), best-effort."""
    privileged.run_provision(project.slug, project.path)
    ensure_topic(project)


def ensure_topic(project: Project) -> None:
    """Crea el forum topic del proyecto y guarda telegram_topic_id. Best-effort:
    si Telegram no está configurado o falla, no bloquea el provisioning."""
    if project.telegram_topic_id or not settings.TELEGRAM_BOT_TOKEN:
        return
    chat_id = Config.get("tg_chat_id")
    if not chat_id:
        return
    try:
        tid = tg.create_forum_topic(chat_id, project.name or project.slug)
    except tg.TelegramError as exc:
        log.warning("no se pudo crear topic para %s: %s", project.slug, exc)
        return
    project.telegram_topic_id = tid
    project.save(update_fields=["telegram_topic_id", "updated_at"])


def archive_project(project: Project) -> None:
    """Worker(s) down + status archived. Datos y archivos intactos. Re-render
    del resto para que dejen de negar dirs de un proyecto ya inactivo."""
    for session in Session.objects.filter(project=project).exclude(
        status__in=[Session.Status.STOPPED, Session.Status.CRASHED]
    ):
        session_svc.stop_session(session)
    project.status = Project.Status.ARCHIVED
    project.save(update_fields=["status", "updated_at"])
    privileged.run_render()