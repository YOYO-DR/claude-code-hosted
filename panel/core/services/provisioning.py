"""Ciclo de vida de proyectos (Fase 2 §5). Crear un proyecto materializa su
directorio (`git init`, chown a `agents`) y re-renderiza TODOS los activos (las
deny obligatorias dinámicas de cada proyecto dependen de la lista completa).
Archivar detiene sus sesiones pero no borra datos ni archivos.

Las operaciones privilegiadas (chown, leer panel.env) se delegan en helpers vía
sudo — ver panel.core.services.privileged."""

from __future__ import annotations

from panel.core.models import Project, Session
from panel.core.services import privileged
from panel.core.services import sessions as session_svc


def provision_project(project: Project) -> None:
    """Directorio + git init + chown agents + render de todos. Idempotente."""
    privileged.run_provision(project.slug, project.path)


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