"""Provisioning §5 Fase 2: crear proyecto = dir + git init + render. Y el badge
'reinicio requerido' (needs_restart) por cambio de MCP/perfil tras arrancar."""

from __future__ import annotations

from pathlib import Path

import pytest
from django.utils import timezone

from panel.core.models import McpServer, ModelProfile, PermissionPolicy, Project, Session
from panel.core.services import provisioning
from panel.core.services import sessions as session_svc

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _paths(tmp_path, settings):
    settings.AGENTS_HOME = tmp_path / "agents"
    settings.PROJECTS_ROOT = tmp_path / "srv"
    return tmp_path


def _project(slug, tmp_path):
    return Project.objects.create(
        slug=slug,
        name=slug.title(),
        path=str(tmp_path / "srv" / slug),
        model_profile=ModelProfile.objects.create(
            name=f"prof-{slug}", provider=ModelProfile.Provider.ANTHROPIC, model="m"
        ),
        permission_policy=PermissionPolicy.objects.create(name=f"pol-{slug}"),
    )


def test_provision_creates_dir_git_and_render(tmp_path):
    project = _project("alpha", tmp_path)
    provisioning.provision_project(project)
    root = Path(project.path)
    assert (root / ".git").is_dir()
    assert (root / ".claude" / "settings.json").is_file()
    assert (root / ".mcp.json").is_file()


def test_provision_idempotent(tmp_path):
    project = _project("alpha", tmp_path)
    provisioning.provision_project(project)
    provisioning.provision_project(project)  # no revienta con .git existente
    assert (Path(project.path) / ".git").is_dir()


def test_needs_restart_on_mcp_change(tmp_path):
    project = _project("alpha", tmp_path)
    # La sesión arranca DESPUÉS de que la config del proyecto ya existe.
    session = Session.objects.create(project=project, started_at=timezone.now())
    assert session_svc.needs_restart(session) is False
    McpServer.objects.create(
        name="ports",
        scope=McpServer.Scope.PROJECT,
        project=project,
        transport=McpServer.Transport.HTTP,
        config={"url": "http://x"},
    )
    assert session_svc.needs_restart(session) is True


def test_needs_restart_false_before_start(tmp_path):
    project = _project("alpha", tmp_path)
    session = Session.objects.create(project=project)  # started_at None
    McpServer.objects.create(
        name="ports",
        scope=McpServer.Scope.GLOBAL,
        transport=McpServer.Transport.HTTP,
        config={"url": "http://x"},
    )
    assert session_svc.needs_restart(session) is False
