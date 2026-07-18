"""Constantes de política. Las deny obligatorias viven en código y el renderer
las inyecta SIEMPRE (no editables desde la UI). Ver §3.

La sintaxis exacta de patrones se valida contra la doc de Claude Code al
implementar el renderer (Fase 2); si difiere, se ajusta y se documenta en
DECISIONS.md."""

from __future__ import annotations

MANDATORY_DENY: list[str] = [
    "Read(./.env*)",
    "Read(//home/agents/.ssh/**)",
    "Read(//home/agents/.claude/**)",
    "Edit(//etc/**)",
    "Write(//etc/**)",
    "Read(//opt/panel/**)",
    "Write(//opt/panel/**)",
]


def deny_for_other_projects(slug: str, all_slugs: list[str]) -> list[str]:
    """Deny dinámicas: cada proyecto niega los dirs de los demás. Generado por
    el renderer al materializar `slug`, para todos los `all_slugs` != slug."""
    rules: list[str] = []
    for other in all_slugs:
        if other == slug:
            continue
        rules.append(f"Read(//srv/projects/{other}/**)")
        rules.append(f"Write(//srv/projects/{other}/**)")
    return rules
