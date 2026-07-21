"""Ciclo de vida de proyectos (Fase 2 §5). Crear un proyecto materializa su
directorio (`git init`, chown a `agents`) y re-renderiza TODOS los activos (las
deny obligatorias dinámicas de cada proyecto dependen de la lista completa).
Archivar detiene sus sesiones pero no borra datos ni archivos.

Las operaciones privilegiadas (chown, leer panel.env) se delegan en helpers vía
sudo — ver panel.core.services.privileged."""

from __future__ import annotations

import logging
from textwrap import dedent

from django.conf import settings

from panel.core.models import Config, Project, Session
from panel.core.services import github, privileged
from panel.core.services import sessions as session_svc
from panel.core.services import telegram as tg

log = logging.getLogger("provisioning")

AGENT_BRANCH = "agent/{slug}"


def _render_agents_md(project: Project) -> str:
    """Genera el contenido de AGENTS.md para el proyecto. El SDK de Claude Code
    lo lee automáticamente al arrancar en `project.path`. Propiedad del panel:
    se regenera en cada provision (idempotente). El agente debe usar NOTES.md
    si quiere anotar cosas propias (ver DENY_MSG en workers/session_worker)."""
    repo_line = (
        f"- **Repo**: `{project.github_repo}` (rama `{AGENT_BRANCH.format(slug=project.slug)}`)"
        if project.github_repo
        else "- **Repo**: (ninguno — directorio local con git init)"
    )
    policy = project.permission_policy
    policy_mode = policy.mode if policy else "—"
    policy_name = policy.name if policy else "—"
    return dedent(
        f"""\
        # AGENTS.md — generado por el panel

        > **NO EDITAR.** Este archivo es propiedad del panel y se sobreescribe
        > en cada provision del proyecto. Si quieres anotar algo propio, usa
        > `NOTES.md`.

        ## Proyecto

        - **Nombre**: {project.name}
        - **Slug**: `{project.slug}`
        - **Path de trabajo**: `{project.path}`
        {repo_line}

        ## Permisos

        - **Policy**: `{policy_name}` (modo `{policy_mode}`)
        - Cualquier tool use que no esté en la allowlist requiere aprobación
          web/Telegram. Si se deniega, continúa con lo que no la requiera y
          documenta el bloqueo en `NOTES.md`.

        ## Convenciones

        - El directorio de trabajo es `{project.path}`. Todo archivo que crees
          debe vivir bajo ese path.
        - No instales dependencias globales; si necesitas una, documéntala en
          `NOTES.md` y propón al operador.
        """
    )


def provision_project(project: Project) -> None:
    """Provisiona el proyecto: clona desde GitHub (rama agent/<slug>) si tiene
    repo activo y hay token; si no, dir vacío con git init. Crea el topic de
    Telegram (best-effort) y escribe AGENTS.md. Idempotente.

    D13: tras el clone exitoso, valida si el PAT tiene push sobre el repo.
    Si NO, marca `github_warn_no_push=True` en el proyecto para que la UI
    muestre un banner persistente (no bloquea: el operador decide)."""
    if project.github_repo and project.github_enabled and github.has_token():
        token = github.get_token()
        if token:
            branch = AGENT_BRANCH.format(slug=project.slug)
            privileged.run_clone(project.path, project.github_repo, branch, token)
            privileged.write_agents_md(project.path, _render_agents_md(project))
            ensure_topic(project)
            _check_and_flag_push_access(project)
            return
    privileged.run_provision(project.slug, project.path)
    privileged.write_agents_md(project.path, _render_agents_md(project))


def _check_and_flag_push_access(project: Project) -> None:
    """Best-effort: si podemos hablar con GitHub, miramos permissions.push
    del repo del proyecto y marcamos github_warn_no_push si falta."""
    if not project.github_repo or not github.has_token():
        return
    token = github.get_token()
    if not token:
        return
    try:
        ok, _ = github.check_push_access(token, project.github_repo)
    except Exception:  # noqa: BLE001 — red/idempotencia
        return
    if project.github_warn_no_push != (not ok):
        project.github_warn_no_push = not ok
        project.save(update_fields=["github_warn_no_push", "updated_at"])
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
    """Worker(s) down + status archived. Datos y archivos intactos. Borra
    AGENTS.md para que no quede un contexto "vivo" en un dir inactivo.
    Re-render del resto para que dejen de negar dirs de un proyecto archivado."""
    for session in Session.objects.filter(project=project).exclude(
        status__in=[Session.Status.STOPPED, Session.Status.CRASHED]
    ):
        session_svc.stop_session(session)
    project.status = Project.Status.ARCHIVED
    project.save(update_fields=["status", "updated_at"])
    privileged.remove_agents_md(project.path)
    privileged.run_render()


def purge_project(project: Project, *, purge_sessions: bool = True) -> None:
    """SP4: hard-delete. Borra sesiones en cascada, purga el dir en disco, y
    ELIMINA la fila del proyecto (no soft-delete). El slug queda libre para
    un recreate. La auditoría mínima (nombre+slug+cuándo) vive en el log de
    Django (LOGGING → journalctl) + los backups de Postgres.

    Devuelve None. El caller no debe usar la instancia `project` después.
    """
    if project.status not in (Project.Status.ARCHIVED, Project.Status.ACTIVE):
        # Idempotente si ya está borrado.
        return
    # Para sesiones activas antes de tocar nada.
    for session in Session.objects.filter(project=project).exclude(
        status__in=[Session.Status.STOPPED, Session.Status.CRASHED]
    ):
        session_svc.stop_session(session)
    if purge_sessions:
        # Cascade: borrar sesiones + eventos + permission requests.
        Session.objects.filter(project=project).delete()
    # Purga el dir.
    privileged.purge_project_files(project.path)
    # Hard-delete la fila. Capturar pk+slug antes para el audit log.
    import logging
    log = logging.getLogger("provisioning")
    slug = project.slug
    project_id = project.pk
    project.delete()
    log.warning(
        "project purge: slug=%s id=%s purged_sessions=%s purged_files=True",
        slug, project_id, purge_sessions,
    )
    # Re-render para que las deny lists reflejen el purge.
    privileged.run_render()


def recreate_project(archived: Project) -> Project:
    """SP4: dado un Project archived (o deleted), crea uno NUEVO con mismo
    slug + FKs + github_repo. Hard-delea el viejo (incluye sesiones y dir) y
    re-clona desde el repo original. Devuelve el nuevo (status=active).

    Si el repo no está configurado, hace `git init` local en vez de clone.
    """
    if archived.status not in (Project.Status.ARCHIVED, Project.Status.DELETED):
        raise ValueError(
            f"solo se puede recrear desde archived/deleted; status={archived.status}"
        )
    # Capturar FKs y campos antes del delete (la fila desaparece).
    snapshot = {
        "slug": archived.slug,
        "name": archived.name,
        "path": archived.path,
        "model_profile": archived.model_profile,
        "permission_policy": archived.permission_policy,
        "github_repo": archived.github_repo,
        "github_enabled": archived.github_enabled,
        "github_warn_no_push": archived.github_warn_no_push,
    }
    # 1) Purga el viejo: hard-delete de la fila + sesiones + dir.
    purge_project(archived, purge_sessions=True)
    # 2) Crea el nuevo (status default ACTIVE).
    new = Project.objects.create(**snapshot)
    # 3) Provisiona (clone si hay repo, si no git init local).
    try:
        provision_project(new)
    except privileged.ProvisioningError as exc:
        # Rollback: borrar la fila nueva + relanzar.
        new.delete()
        raise
    new.refresh_from_db()
    return new