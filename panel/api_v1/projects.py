"""Projects + browser de archivos + diff (FASE C.3 + C.5).

Los endpoints tree/file/diff leen del filesystem como user `panel`. Por
seguridad (FASE C.5 checklist MIGRATION1 §4.4):

  1. Validamos `slug` → `Project.path` contra la DB.
  2. Canonicalizamos el path con `realpath()` y comprobamos que sigue
     dentro de `Project.path` (anti symlink/`..`).
  3. `file` tiene cap de tamaño (100 KB por defecto); binarios → solo
     metadata.
  4. `diff` corre `git diff` (solo lectura) en el path del proyecto.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from django.http import HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET

from panel.core.models import Project

from .auth import require_verified_json

MAX_FILE_BYTES = 100 * 1024  # 100 KB
BINARY_SNIFF_BYTES = 8192


def _serialize_project(p: Project) -> dict:
    return {
        "slug": p.slug,
        "name": p.name,
        "path": p.path,
        "status": p.status,
        "github_repo": p.github_repo,
        "github_enabled": p.github_enabled,
        "github_warn_no_push": p.github_warn_no_push,
    }


@require_GET
@require_verified_json
def list_projects(request: HttpRequest) -> JsonResponse:
    qs = Project.objects.filter(status=Project.Status.ACTIVE).order_by("slug")
    return JsonResponse([_serialize_project(p) for p in qs], safe=False)


@require_GET
@require_verified_json
def project_detail(request: HttpRequest, slug: str) -> JsonResponse:
    p = get_object_or_404(Project, slug=slug, status=Project.Status.ACTIVE)
    return JsonResponse(_serialize_project(p))


def _safe_resolve(project: Project, rel_path: str | None) -> Path:
    """Devuelve el Path absoluto y validado, o levanta ValueError si el
    path se escapa del proyecto (path traversal / symlink fuera)."""
    base = Path(project.path).resolve()
    if not rel_path or rel_path == ".":
        return base
    # Bloquea intentos obvios de traversal. realpath resuelve symlinks y
    # `..`. Si el resultado está fuera de base, ValueError.
    candidate = (base / rel_path).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise ValueError(f"path fuera del proyecto: {rel_path!r}") from exc
    return candidate


@require_GET
@require_verified_json
def project_tree(request: HttpRequest, slug: str) -> JsonResponse:
    """GET /api/v1/projects/<slug>/tree?path=<rel>"""
    p = get_object_or_404(Project, slug=slug, status=Project.Status.ACTIVE)
    rel = request.GET.get("path", ".")
    try:
        target = _safe_resolve(p, rel)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=403)
    if not target.exists():
        return JsonResponse({"error": "path no existe"}, status=404)
    if not target.is_dir():
        return JsonResponse({"error": "no es un directorio"}, status=400)
    try:
        entries = sorted(target.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
    except PermissionError:
        return JsonResponse({"error": "sin permisos"}, status=403)
    out = []
    for e in entries[:500]:  # cap razonable
        try:
            st = e.stat()
        except OSError:
            continue
        out.append({
            "name": e.name,
            "is_dir": e.is_dir(),
            "size": st.st_size,
        })
    return JsonResponse({
        "path": rel,
        "entries": out,
    })


@require_GET
@require_verified_json
def project_file(request: HttpRequest, slug: str) -> JsonResponse:
    """GET /api/v1/projects/<slug>/file?path=<rel>"""
    p = get_object_or_404(Project, slug=slug, status=Project.Status.ACTIVE)
    rel = request.GET.get("path")
    if not rel:
        return JsonResponse({"error": "path requerido"}, status=400)
    try:
        target = _safe_resolve(p, rel)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=403)
    if not target.exists() or not target.is_file():
        return JsonResponse({"error": "archivo no existe"}, status=404)
    try:
        size = target.stat().st_size
    except OSError:
        return JsonResponse({"error": "no se pudo leer"}, status=500)
    # Binario: solo metadata.
    try:
        with open(target, "rb") as f:
            sniff = f.read(BINARY_SNIFF_BYTES)
        is_binary = b"\x00" in sniff
    except OSError:
        is_binary = True
    if is_binary:
        return JsonResponse({
            "path": rel,
            "size": size,
            "is_binary": True,
            "content": None,
        })
    if size > MAX_FILE_BYTES:
        with open(target, "rb") as f:
            content = f.read(MAX_FILE_BYTES).decode("utf-8", errors="replace")
        truncated = True
    else:
        with open(target, "rb") as f:
            content = f.read().decode("utf-8", errors="replace")
        truncated = False
    return JsonResponse({
        "path": rel,
        "size": size,
        "is_binary": False,
        "truncated": truncated,
        "content": content,
    })


@require_GET
@require_verified_json
def project_diff(request: HttpRequest, slug: str) -> JsonResponse:
    """GET /api/v1/projects/<slug>/diff?path=<rel opcional>
    `git diff` unstaged+staged del proyecto (o de un archivo concreto)."""
    p = get_object_or_404(Project, slug=slug, status=Project.Status.ACTIVE)
    rel = request.GET.get("path")
    if rel:
        try:
            _ = _safe_resolve(p, rel)
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=403)
    cmd = ["git", "-C", p.path, "diff", "--no-color"]
    if rel:
        cmd.append("--")
        cmd.append(rel)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired:
        return JsonResponse({"error": "git diff timeout"}, status=504)
    except FileNotFoundError:
        return JsonResponse({"error": "git no instalado"}, status=500)
    if proc.returncode != 0:
        return JsonResponse({
            "error": proc.stderr.strip() or f"git diff rc={proc.returncode}",
        }, status=500)
    # También status de dirty (untracked / staged).
    status_proc = subprocess.run(
        ["git", "-C", p.path, "status", "--porcelain"],
        capture_output=True, text=True, timeout=10,
    )
    return JsonResponse({
        "path": rel or None,
        "diff": proc.stdout,
        "dirty": bool(status_proc.stdout.strip()),
    })