"""Renderer §4.3: la DB es la fuente de verdad; esto la materializa a los
archivos que Claude Code lee. Nadie edita esos archivos a mano.

`render_project` / `render_global` son funciones puras (DB -> {path: contenido}).
`apply_render` los escribe atómicamente (tmp + os.replace). El env del modelo
(tokens) NUNCA se materializa: lo inyecta el worker vía ClaudeAgentOptions.env.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from django.conf import settings
from django.db.models import Q

from panel.core.constants import MANDATORY_DENY, deny_for_other_projects
from panel.core.models import McpServer, Project, Skill

# Seed del prompt global (§7). Vive como archivo de datos; editable desde la web
# en una fase posterior. ponytail: sin modelo/UI de edición hasta que se pida —
# el gate no lo prueba.
GLOBAL_PROMPT = (Path(__file__).resolve().parent / "prompts" / "global_claude.md").read_text(
    encoding="utf-8"
)


def _agents_home() -> Path:
    return Path(settings.AGENTS_HOME)


def _skill_md(skill: Skill) -> str:
    """SKILL.md con frontmatter. name/description se escapan como JSON (válido
    como escalar YAML) para tolerar espacios, comillas y unicode."""
    name = json.dumps(skill.name, ensure_ascii=False)
    return f"---\nname: {name}\ndescription: {name}\n---\n\n{skill.content}\n"


def _settings_json(project: Project, all_slugs: list[str]) -> str:
    policy = project.permission_policy
    deny = list(MANDATORY_DENY)
    deny += deny_for_other_projects(project.slug, all_slugs)
    deny += list(policy.deny_rules or [])
    allow = list(policy.allowed_tools or [])
    data = {"permissions": {"allow": allow, "deny": deny}}
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _mcp_json(project: Project) -> str:
    servers: dict[str, dict] = {}
    qs = (
        McpServer.objects.filter(enabled=True)
        .filter(Q(scope=McpServer.Scope.GLOBAL) | Q(scope=McpServer.Scope.PROJECT, project=project))
        .order_by("name")
    )
    for m in qs:
        cfg = m.config or {}
        if m.transport == McpServer.Transport.STDIO:
            entry: dict = {}
            if "command" in cfg:
                entry["command"] = cfg["command"]
            if "args" in cfg:
                entry["args"] = cfg["args"]
            if "env" in cfg:
                entry["env"] = cfg["env"]
        else:  # http
            entry = {"type": "http", "url": cfg.get("url", "")}
        servers[m.name] = entry
    return json.dumps({"mcpServers": servers}, indent=2, ensure_ascii=False) + "\n"


def _project_claude_md(project: Project) -> str:
    return f"# {project.name}\n\nProyecto `{project.slug}`. Trabaja dentro de este directorio.\n"


def _project_skills(project: Project) -> dict[str, str]:
    out: dict[str, str] = {}
    base = Path(project.path) / ".claude" / "skills"
    for s in project.skills.filter(enabled=True, scope=Skill.Scope.PROJECT).order_by("name"):
        out[str(base / s.name / "SKILL.md")] = _skill_md(s)
    return out


def render_project(project: Project, all_slugs: list[str] | None = None) -> dict[str, str]:
    """Archivos específicos del proyecto. Puro: no toca disco."""
    if all_slugs is None:
        all_slugs = sorted(
            Project.objects.filter(status=Project.Status.ACTIVE).values_list("slug", flat=True)
        )
    root = Path(project.path)
    rendered = {
        str(root / "CLAUDE.md"): _project_claude_md(project),
        str(root / ".claude" / "settings.json"): _settings_json(project, all_slugs),
        str(root / ".mcp.json"): _mcp_json(project),
    }
    rendered.update(_project_skills(project))
    return rendered


def render_global() -> dict[str, str]:
    """Archivos compartidos en ~/.claude del usuario agents. Puro."""
    home = _agents_home() / ".claude"
    rendered = {str(home / "CLAUDE.md"): GLOBAL_PROMPT}
    for s in Skill.objects.filter(enabled=True, scope=Skill.Scope.GLOBAL).order_by("name"):
        rendered[str(home / "skills" / s.name / "SKILL.md")] = _skill_md(s)
    return rendered


def apply_render(rendered: dict[str, str]) -> None:
    """Escritura atómica por archivo: tmp + os.replace (misma partición)."""
    for path, content in rendered.items():
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, p)


def _prune_skills(projects: list[Project]) -> None:
    """Borra dirs de skills que ya no están habilitadas (skill deshabilitada o
    eliminada, o cambio de scope). Estrictamente bajo .../skills/."""
    import shutil

    def prune(skills_dir: Path, keep: set[str]) -> None:
        if not skills_dir.is_dir():
            return
        for child in skills_dir.iterdir():
            if child.is_dir() and child.name not in keep:
                shutil.rmtree(child)

    global_keep = set(
        Skill.objects.filter(enabled=True, scope=Skill.Scope.GLOBAL).values_list("name", flat=True)
    )
    prune(_agents_home() / ".claude" / "skills", global_keep)
    for project in projects:
        keep = set(
            project.skills.filter(enabled=True, scope=Skill.Scope.PROJECT).values_list(
                "name", flat=True
            )
        )
        prune(Path(project.path) / ".claude" / "skills", keep)


def render_all() -> None:
    """Materializa todo: global + cada proyecto activo (con las deny dinámicas
    de todos contra todos). Idempotente."""
    projects = list(
        Project.objects.filter(status=Project.Status.ACTIVE).select_related(
            "permission_policy", "model_profile"
        )
    )
    all_slugs = sorted(p.slug for p in projects)
    rendered = render_global()
    for project in projects:
        rendered.update(render_project(project, all_slugs))
    apply_render(rendered)
    _prune_skills(projects)
