"""Ciclo de vida de proyectos (Fase 2 §5). Crear un proyecto materializa su
directorio (`git init`) y re-renderiza TODOS los activos (las deny obligatorias
dinámicas de cada proyecto dependen de la lista completa). Archivar detiene sus
sesiones pero no borra datos ni archivos."""

from __future__ import annotations

import subprocess
from pathlib import Path

from panel.core import renderer
from panel.core.models import Project, Session
from panel.core.services import sessions as session_svc


def provision_project(project: Project) -> None:
    """Directorio + git init + render de todos. Idempotente."""
    path = Path(project.path)
    path.mkdir(parents=True, exist_ok=True)
    if not (path / ".git").exists():
        subprocess.run(["git", "init", "-q", str(path)], check=True)
    renderer.render_all()


def archive_project(project: Project) -> None:
    """Worker(s) down + status archived. Datos y archivos intactos. Re-render
    del resto para que dejen de negar dirs de un proyecto ya inactivo."""
    for session in Session.objects.filter(project=project).exclude(
        status__in=[Session.Status.STOPPED, Session.Status.CRASHED]
    ):
        session_svc.stop_session(session)
    project.status = Project.Status.ARCHIVED
    project.save(update_fields=["status", "updated_at"])
    renderer.render_all()
