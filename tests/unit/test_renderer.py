"""Renderer §4.3 — Gate 2: golden files, doble render sin diff, unicode/espacios
escapados, deny obligatorias + dinámicas, MCP y skills por scope."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from panel.core import renderer
from panel.core.constants import MANDATORY_DENY
from panel.core.models import McpServer, ModelProfile, PermissionPolicy, Project, Skill

pytestmark = pytest.mark.django_db


@pytest.fixture
def home(tmp_path, settings):
    agents = tmp_path / "agents"
    settings.AGENTS_HOME = agents
    settings.PROJECTS_ROOT = tmp_path / "srv"
    return agents


def _profile(name="p"):
    return ModelProfile.objects.create(
        name=name, provider=ModelProfile.Provider.ANTHROPIC, model="m"
    )


def _project(slug, tmp_path, policy=None, profile=None):
    return Project.objects.create(
        slug=slug,
        name=slug.title(),
        path=str(tmp_path / "srv" / slug),
        model_profile=profile or _profile(f"prof-{slug}"),
        permission_policy=policy
        or PermissionPolicy.objects.create(name=f"pol-{slug}", mode=PermissionPolicy.Mode.APPROVE),
    )


def test_double_render_no_diff(home, tmp_path):
    _project("alpha", tmp_path)
    _project("beta", tmp_path)
    renderer.render_all()
    snap1 = {p: Path(p).read_bytes() for p in _all_files(tmp_path)}
    renderer.render_all()
    snap2 = {p: Path(p).read_bytes() for p in _all_files(tmp_path)}
    assert snap1 == snap2 and snap1  # byte a byte, y no vacío


def test_mandatory_and_dynamic_deny(home, tmp_path):
    _project("alpha", tmp_path)
    _project("beta", tmp_path)
    renderer.render_all()
    settings_a = json.loads(
        (tmp_path / "srv" / "alpha" / ".claude" / "settings.json").read_text()
    )
    deny = settings_a["permissions"]["deny"]
    for rule in MANDATORY_DENY:
        assert rule in deny
    # alpha niega el dir de beta, no el suyo.
    assert "Read(//srv/projects/beta/**)" in deny
    assert "Write(//srv/projects/beta/**)" in deny
    assert "Read(//srv/projects/alpha/**)" not in deny


def test_policy_allow_and_deny_merged(home, tmp_path):
    policy = PermissionPolicy.objects.create(
        name="custom",
        mode=PermissionPolicy.Mode.APPROVE,
        allowed_tools=["Bash(git commit:*)"],
        deny_rules=["Read(./secret.txt)"],
    )
    _project("alpha", tmp_path, policy=policy)
    renderer.render_all()
    data = json.loads((tmp_path / "srv" / "alpha" / ".claude" / "settings.json").read_text())
    assert data["permissions"]["allow"] == ["Bash(git commit:*)"]
    assert "Read(./secret.txt)" in data["permissions"]["deny"]
    assert MANDATORY_DENY[0] in data["permissions"]["deny"]


def test_mcp_json_stdio_and_http(home, tmp_path):
    project = _project("alpha", tmp_path)
    McpServer.objects.create(
        name="ports",
        scope=McpServer.Scope.GLOBAL,
        transport=McpServer.Transport.STDIO,
        config={"command": "python", "args": ["-m", "mcp_ports"], "env": {"X": "1"}},
    )
    McpServer.objects.create(
        name="local-http",
        scope=McpServer.Scope.PROJECT,
        project=project,
        transport=McpServer.Transport.HTTP,
        config={"url": "http://127.0.0.1:8080"},
    )
    renderer.render_all()
    mcp = json.loads((tmp_path / "srv" / "alpha" / ".mcp.json").read_text())
    assert mcp["mcpServers"]["ports"] == {
        "command": "python",
        "args": ["-m", "mcp_ports"],
        "env": {"X": "1"},
    }
    assert mcp["mcpServers"]["local-http"] == {"type": "http", "url": "http://127.0.0.1:8080"}


def test_project_mcp_isolated(home, tmp_path):
    a = _project("alpha", tmp_path)
    _project("beta", tmp_path)
    McpServer.objects.create(
        name="only-alpha",
        scope=McpServer.Scope.PROJECT,
        project=a,
        transport=McpServer.Transport.HTTP,
        config={"url": "http://x"},
    )
    renderer.render_all()
    mcp_a = json.loads((tmp_path / "srv" / "alpha" / ".mcp.json").read_text())
    mcp_b = json.loads((tmp_path / "srv" / "beta" / ".mcp.json").read_text())
    assert "only-alpha" in mcp_a["mcpServers"]
    assert "only-alpha" not in mcp_b["mcpServers"]


def test_skills_scope_visibility(home, tmp_path):
    a = _project("alpha", tmp_path)
    _project("beta", tmp_path)
    Skill.objects.create(name="global-skill", scope=Skill.Scope.GLOBAL, content="g")
    Skill.objects.create(
        name="alpha-skill", scope=Skill.Scope.PROJECT, project=a, content="a"
    )
    renderer.render_all()
    # Global skill vive en ~/.claude, visible para todos.
    assert (home / ".claude" / "skills" / "global-skill" / "SKILL.md").exists()
    # Skill de proyecto solo en su propio dir.
    assert (tmp_path / "srv" / "alpha" / ".claude" / "skills" / "alpha-skill" / "SKILL.md").exists()
    assert not (
        tmp_path / "srv" / "beta" / ".claude" / "skills" / "alpha-skill" / "SKILL.md"
    ).exists()


def test_disabled_skill_pruned(home, tmp_path):
    a = _project("alpha", tmp_path)
    skill = Skill.objects.create(
        name="tmp-skill", scope=Skill.Scope.PROJECT, project=a, content="x"
    )
    renderer.render_all()
    skill_dir = tmp_path / "srv" / "alpha" / ".claude" / "skills" / "tmp-skill"
    assert skill_dir.exists()
    skill.enabled = False
    skill.save()
    renderer.render_all()
    assert not skill_dir.exists()


def test_unicode_and_spaces_escaped(home, tmp_path):
    project = _project("alpha", tmp_path)
    McpServer.objects.create(
        name="mi servidor ñ",
        scope=McpServer.Scope.PROJECT,
        project=project,
        transport=McpServer.Transport.HTTP,
        config={"url": "http://例え.test/路径 con espacio"},
    )
    Skill.objects.create(
        name='skill "raro": ñ', scope=Skill.Scope.PROJECT, project=project, content="cuerpo"
    )
    renderer.render_all()
    raw_mcp = (tmp_path / "srv" / "alpha" / ".mcp.json").read_text()
    mcp = json.loads(raw_mcp)  # parsea sin error => escapado correcto
    assert "mi servidor ñ" in mcp["mcpServers"]
    assert mcp["mcpServers"]["mi servidor ñ"]["url"] == "http://例え.test/路径 con espacio"
    # SKILL.md: el name con comillas/dos-puntos va como JSON (YAML escalar válido).
    skill_md = (
        tmp_path / "srv" / "alpha" / ".claude" / "skills" / 'skill "raro": ñ' / "SKILL.md"
    ).read_text()
    assert 'name: "skill \\"raro\\": ñ"' in skill_md


def test_atomic_apply_overwrites(home, tmp_path):
    _project("alpha", tmp_path)
    renderer.render_all()
    target = tmp_path / "srv" / "alpha" / ".mcp.json"
    target.write_text("STALE")
    renderer.render_all()
    assert json.loads(target.read_text()) == {"mcpServers": {}}
    # sin .tmp huérfanos
    assert not list(target.parent.glob("*.tmp"))


def _all_files(tmp_path):
    files = []
    for root in (tmp_path / "agents", tmp_path / "srv"):
        if root.exists():
            files += [str(p) for p in sorted(root.rglob("*")) if p.is_file()]
    return files
