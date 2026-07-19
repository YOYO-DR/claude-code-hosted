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


def test_provision_writes_agents_md(tmp_path):
    project = _project("alpha", tmp_path)
    project.github_repo = "owner/alpha-repo"
    project.github_enabled = False  # sin token: cae al path dir vacío
    project.save()
    provisioning.provision_project(project)
    agents_md = Path(project.path) / "AGENTS.md"
    assert agents_md.is_file()
    body = agents_md.read_text(encoding="utf-8")
    assert "NO EDITAR" in body
    assert "alpha" in body
    assert project.path in body  # el path real del fixture, no el default
    assert "owner/alpha-repo" in body
    assert "agent/alpha" in body  # nombre de la rama


def test_agents_md_idempotent_overwrites(tmp_path):
    project = _project("beta", tmp_path)
    provisioning.provision_project(project)
    Path(project.path, "AGENTS.md").write_text("CONTENIDO ROTO", encoding="utf-8")
    provisioning.provision_project(project)
    body = Path(project.path, "AGENTS.md").read_text(encoding="utf-8")
    assert "CONTENIDO ROTO" not in body
    assert "NO EDITAR" in body


def test_archive_removes_agents_md(tmp_path):
    project = _project("gamma", tmp_path)
    provisioning.provision_project(project)
    assert (Path(project.path) / "AGENTS.md").is_file()
    provisioning.archive_project(project)
    assert not (Path(project.path) / "AGENTS.md").exists()
    project.refresh_from_db()
    assert project.status == Project.Status.ARCHIVED


def test_privileged_calls_write_agents_helper(monkeypatch):
    """write_agents_md debe mandar el contenido por stdin al helper sudo."""
    from panel.core.services import privileged

    calls = []
    monkeypatch.setattr(privileged.os, "geteuid", lambda: 1000, raising=False)
    monkeypatch.setattr(privileged.shutil, "which", lambda _: "/usr/bin/sudo")
    monkeypatch.setattr(privileged.os.path, "exists", lambda _: True)

    def fake_run(argv, **kw):
        calls.append((argv, kw.get("input")))
        return None

    monkeypatch.setattr(privileged.subprocess, "run", fake_run)
    privileged.write_agents_md("/srv/projects/x", "# AGENTS\n")
    privileged.remove_agents_md("/srv/projects/x")
    assert calls[0] == (
        ["sudo", "-n", privileged.PROVISION_HELPER, "write-agents", "/srv/projects/x"],
        "# AGENTS\n",
    )
    assert calls[1][0] == [
        "sudo", "-n", privileged.PROVISION_HELPER, "remove-agents", "/srv/projects/x",
    ]


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


def test_privileged_uses_sudo_helper_when_not_root(monkeypatch):
    """panel (no root) + sudo + helper presente -> sudo al helper, NO render en
    proceso."""
    from panel.core.services import privileged

    calls = []
    monkeypatch.setattr(privileged.os, "geteuid", lambda: 1000, raising=False)
    monkeypatch.setattr(privileged.shutil, "which", lambda _: "/usr/bin/sudo")
    monkeypatch.setattr(privileged.os.path, "exists", lambda _: True)
    monkeypatch.setattr(privileged.subprocess, "run", lambda *a, **k: calls.append(a[0]))
    privileged.run_provision("alpha", "/srv/projects/alpha")
    privileged.run_render()
    assert calls == [
        ["sudo", "-n", privileged.PROVISION_HELPER, "provision", "alpha", "/srv/projects/alpha"],
        ["sudo", "-n", privileged.RENDER_HELPER],
    ]


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
