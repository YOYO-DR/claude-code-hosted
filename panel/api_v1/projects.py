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

import json
import subprocess
from pathlib import Path

from django.http import HttpRequest, JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from panel.core.models import PermissionPolicy, ModelProfile, Project

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
    """GET /api/v1/projects/?status=active|archived|deleted|all
    Default: status=active. SP4: la SPA necesita listar archived para
    mostrar la sección 'Archivados' con acciones de Re-clonar / Eliminar."""
    qs = Project.objects.all().order_by("slug")
    status_param = (request.GET.get("status") or "active").strip().lower()
    if status_param == "all":
        pass
    elif status_param in {c.value for c in Project.Status}:
        qs = qs.filter(status=status_param)
    else:
        qs = qs.filter(status=Project.Status.ACTIVE)
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
    `git diff` unstaged+staged del proyecto (o de un archivo concreto).
    Si el path NO es un repo git, devuelve 200 con `not_a_repo=true` y
    diff vacío (en vez de 500) — el cliente SPA puede mostrar un
    placeholder limpio."""
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
    # Detectar "no es repo" antes de devolver 500. git retorna rc=128 con
    # stderr "fatal: not a git repository (or any of the parent
    # directories): .git" cuando el path no es un repo.
    not_a_repo = (
        proc.returncode != 0
        and "not a git repository" in (proc.stderr or "")
    )
    if proc.returncode != 0 and not not_a_repo:
        return JsonResponse({
            "error": proc.stderr.strip() or f"git diff rc={proc.returncode}",
        }, status=500)
    # También status de dirty (untracked / staged).
    status_proc = subprocess.run(
        ["git", "-C", p.path, "status", "--porcelain"],
        capture_output=True, text=True, timeout=10,
    )
    if not_a_repo:
        return JsonResponse({
            "path": rel or None,
            "diff": "",
            "dirty": False,
            "not_a_repo": True,
        })
    return JsonResponse({
        "path": rel or None,
        "diff": proc.stdout,
        "dirty": bool(status_proc.stdout.strip()),
    })


@require_GET
@require_verified_json
def project_diff_files(request: HttpRequest, slug: str) -> JsonResponse:
    """GET /api/v1/projects/<slug>/diff/files/
    Lista de archivos modificados (unstaged+staged) con +/- counts.
    Lo consume el SPA (ProjectDiff rediseñado): muestra un árbol de
    archivos modificados y permite expandir para ver el diff.
    Formato por archivo: {path, status, additions, deletions, is_binary}
    status ∈ {M=modified, A=added, D=deleted, R=renamed, ??=untracked}
    """
    p = get_object_or_404(Project, slug=slug, status=Project.Status.ACTIVE)
    if not Path(p.path).is_dir():
        return JsonResponse({"files": [], "not_a_repo": True})
    # --name-status da una línea por archivo "M\tpath" o "R\told\tnew"
    # Lo combinamos con --numstat que da "adds\tdels\tpath".
    ns = subprocess.run(
        ["git", "-C", p.path, "diff", "--name-status", "--no-renames"],
        capture_output=True, text=True, timeout=10,
    )
    nm = subprocess.run(
        ["git", "-C", p.path, "diff", "--numstat", "--no-renames"],
        capture_output=True, text=True, timeout=10,
    )
    if ns.returncode != 0 and "not a git repository" in (ns.stderr or ""):
        return JsonResponse({"files": [], "not_a_repo": True})
    if ns.returncode != 0:
        return JsonResponse({
            "files": [],
            "error": (ns.stderr or "").strip() or f"git rc={ns.returncode}",
        })
    # Parse numstat: "+\t-\tpath" (o "-\t-\tpath" para binarios)
    num: dict[str, tuple[int, int]] = {}
    for ln in (nm.stdout or "").splitlines():
        parts = ln.split("\t")
        if len(parts) < 3:
            continue
        a, d, path = parts[0], parts[1], parts[2]
        try:
            adds = int(a)
            dels = int(d)
        except ValueError:
            adds, dels = -1, -1  # binario
        num[path] = (adds, dels)
    files = []
    for ln in (ns.stdout or "").splitlines():
        parts = ln.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0]
        path = parts[-1]
        adds, dels = num.get(path, (-1, -1))
        is_binary = adds < 0 or dels < 0
        files.append({
            "path": path,
            "status": status,
            "additions": max(adds, 0),
            "deletions": max(dels, 0),
            "is_binary": is_binary,
        })
    return JsonResponse({"files": files})


@require_GET
@require_verified_json
def project_diff_file(request: HttpRequest, slug: str) -> JsonResponse:
    """GET /api/v1/projects/<slug>/diff/file/?path=<rel>
    Diff de un solo archivo — el SPA lo usa para lazy-load cuando el
    usuario expande un archivo en ProjectDiff rediseñado."""
    rel = request.GET.get("path", "")
    if not rel:
        return JsonResponse({"error": "Falta ?path="}, status=400)
    p = get_object_or_404(Project, slug=slug, status=Project.Status.ACTIVE)
    try:
        _ = _safe_resolve(p, rel)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=403)
    cmd = ["git", "-C", p.path, "diff", "--no-color", "--", rel]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    not_a_repo = (
        proc.returncode != 0
        and ("not a git repository" in (proc.stderr or "")
             or "cannot change to" in (proc.stderr or ""))
    )
    if not_a_repo:
        return JsonResponse({"path": rel, "diff": "", "not_a_repo": True})
    if proc.returncode != 0:
        return JsonResponse({
            "error": (proc.stderr or "").strip() or f"git rc={proc.returncode}",
        }, status=500)
    return JsonResponse({"path": rel, "diff": proc.stdout})


@require_GET
@require_verified_json
def project_git(request: HttpRequest, slug: str) -> JsonResponse:
    """GET /api/v1/projects/<slug>/git/
    Devuelve la rama actual + flag dirty del repo. Endpoint consumido por
    la pestaña "Rama" del SPA. Maneja "no es repo" / "dubious ownership"
    / path inexistente devolviendo 200 con `not_a_repo=true` para que el
    SPA pueda mostrar un placeholder limpio en lugar de un error.
    """
    p = get_object_or_404(Project, slug=slug, status=Project.Status.ACTIVE)
    if not Path(p.path).is_dir():
        # Path aún no provisionado (clone no llegó a feliz término) o
        # archivado con path limpiado a mano.
        return JsonResponse({
            "branch": None,
            "dirty": False,
            "not_a_repo": True,
        })
    branch_proc = subprocess.run(
        ["git", "-C", p.path, "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, timeout=10,
    )
    # Detectar "no es repo" / "cannot change to" / "dubious ownership".
    stderr = branch_proc.stderr or ""
    not_a_repo = (
        branch_proc.returncode != 0
        and (
            "not a git repository" in stderr
            or "cannot change to" in stderr
        )
    )
    if not_a_repo:
        return JsonResponse({
            "branch": None,
            "dirty": False,
            "not_a_repo": True,
        })
    if branch_proc.returncode != 0:
        # Dubious ownership u otro error — devolvemos 200 con error en
        # el cuerpo para que el SPA pueda mostrar mensaje sin romper.
        return JsonResponse({
            "branch": None,
            "dirty": False,
            "error": stderr.strip() or f"git rc={branch_proc.returncode}",
        })
    status_proc = subprocess.run(
        ["git", "-C", p.path, "status", "--porcelain"],
        capture_output=True, text=True, timeout=10,
    )
    return JsonResponse({
        "branch": branch_proc.stdout.strip(),
        "dirty": bool(status_proc.stdout.strip()),
    })

# ---- Project edit + archive (UX-T.2) ----

# Campos editables de un Project después de creado.
# Inmutables a propósito: `slug` (rompe URLs y logs), `path` (filesystem
# mover de sitio requiere re-clone manual), `status` (se cambia vía archive).
PROJECT_EDITABLE_FIELDS = (
    "name",
    "github_repo",
    "github_enabled",
    "telegram_topic_id",
    "model_profile_id",
    "permission_policy_id",
)


@csrf_exempt
@require_http_methods(["PATCH"])
@require_verified_json
def project_update(request: HttpRequest, slug: str) -> JsonResponse:
    """PATCH /api/v1/projects/<slug>/
    Body JSON con cualquiera de los campos editables. Devuelve el proyecto
    actualizado. Rechaza campos no editables (slug, path, status) con 400.
    """
    p = get_object_or_404(Project, slug=slug, status=Project.Status.ACTIVE)
    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "json inválido"}, status=400)
    if not isinstance(body, dict):
        return JsonResponse({"error": "body debe ser objeto JSON"}, status=400)
    # Defensa: campos inmutables
    forbidden = {"slug", "path", "status"}.intersection(body.keys())
    if forbidden:
        return JsonResponse(
            {"error": f"campos no editables: {sorted(forbidden)}"},
            status=400,
        )
    # Solo aplicar campos conocidos (ignora extras silenciosamente)
    fields = [k for k in PROJECT_EDITABLE_FIELDS if k in body]
    if not fields:
        return JsonResponse(
            {"error": f"body vacío; campos válidos: {list(PROJECT_EDITABLE_FIELDS)}"},
            status=400,
        )
    # Validar FK si vienen
    for fk in ("model_profile_id", "permission_policy_id"):
        if fk in body and not isinstance(body[fk], int):
            return JsonResponse({"error": f"{fk} debe ser int"}, status=400)
    if "model_profile_id" in fields and body["model_profile_id"] is not None:
        if not ModelProfile.objects.filter(pk=body["model_profile_id"]).exists():
            return JsonResponse({"error": "model_profile no existe"}, status=400)
    if "permission_policy_id" in fields and body["permission_policy_id"] is not None:
        if not PermissionPolicy.objects.filter(pk=body["permission_policy_id"]).exists():
            return JsonResponse({"error": "permission_policy no existe"}, status=400)
    for f in fields:
        setattr(p, f, body[f])
    p.save(update_fields=fields)
    return JsonResponse(_serialize_project(p))


@csrf_exempt
@require_http_methods(["DELETE", "POST"])
@require_verified_json
def project_delete(request: HttpRequest, slug: str) -> JsonResponse:
    """DELETE /api/v1/projects/<slug>/  (o POST con body)
    Soft-delete (default): marca el proyecto como ARCHIVED. 409 si tiene
    sesiones activas (running/idle/waiting_approval) — primero pararlas.

    Hard-delete (SP4): body `{"hard": true, "confirm_slug": "<slug>",
    "purge_sessions": true, "purge_files": true}`. Marca status=DELETED,
    opcionalmente borra sesiones+eventos y el dir en disco. Libera el slug
    para un recreate posterior. 400 si `confirm_slug != slug` (defensa
    contra borrado accidental).
    """
    p = get_object_or_404(Project, slug=slug)
    # POST legacy + nuevo body parsing (DELETE también lo soporta).
    if request.method == "DELETE" and not request.body:
        body: dict = {}
    else:
        try:
            body = json.loads(request.body or b"{}")
        except json.JSONDecodeError:
            return JsonResponse({"error": "json inválido"}, status=400)
        if not isinstance(body, dict):
            return JsonResponse({"error": "body debe ser objeto JSON"}, status=400)
    hard = bool(body.get("hard", False))
    active_qs = (
        p.sessions.filter(status__in=("running", "idle", "waiting_approval"))
        .order_by("-created_at")
    )
    if active_qs.exists():
        active = [
            {"id": str(s.id), "status": s.status}
            for s in active_qs
        ]
        return JsonResponse(
            {
                "error": (
                    f"el proyecto tiene {len(active)} sesión(es) activa(s); "
                    "páralas primero antes de archivar"
                ),
                "active_sessions": active,
            },
            status=409,
        )
    if not hard:
        # Soft-delete: comportamiento de antes (FASE 2.5).
        p.status = Project.Status.ARCHIVED
        p.save(update_fields=["status", "updated_at"])
        return JsonResponse({"ok": True, "slug": p.slug, "status": p.status})
    # SP4: hard-delete con confirm + opciones.
    confirm = (body.get("confirm_slug") or "").strip()
    if confirm != slug:
        return JsonResponse(
            {"error": f"confirm_slug debe coincidir con '{slug}' (escribiste '{confirm}')"},
            status=400,
        )
    purge_sessions = bool(body.get("purge_sessions", True))
    purge_files = bool(body.get("purge_files", True))
    from panel.core.services import provisioning as prov_svc
    if purge_files:
        # purge_project ya purga el dir; si no, dejamos el dir intacto.
        prov_svc.purge_project(p, purge_sessions=purge_sessions)
    else:
        # Sin purga de archivos: solo status=DELETED + (opcional) sesiones.
        for session in Session.objects.filter(project=p).exclude(
            status__in=[Session.Status.STOPPED, Session.Status.CRASHED]
        ):
            from panel.core.services import sessions as session_svc
            session_svc.stop_session(session)
        if purge_sessions:
            Session.objects.filter(project=p).delete()
        p.status = Project.Status.DELETED
        p.save(update_fields=["status", "updated_at"])
        from panel.core.services import privileged
        privileged.run_render()
    return JsonResponse({
        "ok": True,
        "slug": p.slug,
        "status": "deleted",
        "purged_sessions": purge_sessions,
        "purged_files": purge_files,
    })


# ---- Project create (UX-T.5) ----

@require_GET
@require_verified_json
def project_form_options(request: HttpRequest) -> JsonResponse:
    """GET /api/v1/projects/form-options/ — ModelProfiles y PermissionPolicies
    disponibles para popular el modal de creación en la SPA. Si github_enabled
    está activo pero no hay token guardado, devuelve flag `gh_token_missing`
    para que el SPA muestre el error antes de enviar."""
    from panel.core.models import ModelProfile, PermissionPolicy
    profiles = [
        {"id": p.id, "name": p.name, "provider": p.provider}
        for p in ModelProfile.objects.all().order_by("name")
    ]
    policies = [
        {"id": p.id, "name": p.name, "mode": p.mode}
        for p in PermissionPolicy.objects.all().order_by("name")
    ]
    from panel.core.services import github as gh_svc
    return JsonResponse({
        "model_profiles": profiles,
        "permission_policies": policies,
        "gh_token_missing": not gh_svc.has_token(),
    })


@csrf_exempt
@require_http_methods(["POST"])
@require_verified_json
def project_create(request: HttpRequest) -> JsonResponse:
    """POST /api/v1/projects/create/
    Body: {name, slug, model_profile_id, permission_policy_id,
           github_repo?, github_enabled?, telegram_topic_id?}
    Crea el Project, lanza provision_project (clone/init + render), y
    devuelve {ok, slug, warnings?}. En caso de ProvisioningError, hace
    rollback del Project (FASE A.5 / D12) y devuelve 400 con `{error}`.
    """
    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "json inválido"}, status=400)
    if not isinstance(body, dict):
        return JsonResponse({"error": "body debe ser objeto JSON"}, status=400)
    name = (body.get("name") or "").strip()
    slug = (body.get("slug") or "").strip().lower()
    if not name or not slug:
        return JsonResponse({"error": "name y slug son requeridos"}, status=400)
    # validación slug
    import re as _re
    if not _re.match(r"^[a-z0-9][a-z0-9-]*$", slug):
        return JsonResponse(
            {"error": "slug debe ser kebab-case (a-z, 0-9, guiones)"},
            status=400,
        )
    if Project.objects.filter(slug=slug).exists():
        # SP4: si existe archived/deleted y el cliente manda force_recreate,
        # delegamos en recreate_project (que hard-delea el viejo + re-clona).
        existing = Project.objects.filter(slug=slug).first()
        if existing and existing.status in (
            Project.Status.ARCHIVED,
            Project.Status.DELETED,
        ) and body.get("force_recreate"):
            from panel.core.services import provisioning as prov_svc
            try:
                new = prov_svc.recreate_project(existing)
            except privileged.ProvisioningError as exc:
                return JsonResponse({"error": str(exc)}, status=400)
            except Exception as exc:
                return JsonResponse(
                    {"error": f"recreate falló: {exc}"}, status=400,
                )
            return JsonResponse(
                {
                    "ok": True,
                    "slug": new.slug,
                    "path": new.path,
                    "recreated_from": str(existing.id),
                    "warnings": (
                        ["El PAT actual no tiene push sobre este repo."]
                        if new.github_warn_no_push else []
                    ),
                },
                status=201,
            )
        if existing and existing.status in (
            Project.Status.ARCHIVED,
            Project.Status.DELETED,
        ) and not body.get("force_recreate"):
            return JsonResponse(
                {
                    "error": (
                        f"ya existe un proyecto '{slug}' con status='{existing.status}'; "
                        "usa POST /api/v1/projects/{slug}/recreate/ o añade "
                        "`force_recreate: true` para reemplazarlo"
                    ).format(slug=slug),
                    "archived_slug": slug,
                    "recreate_available": True,
                },
                status=409,
            )
        return JsonResponse(
            {"error": f"ya existe un proyecto con slug '{slug}'"}, status=409,
        )
    gh_repo = (body.get("github_repo") or "").strip() or None
    gh_enabled = bool(body.get("github_enabled", False))
    if gh_enabled and not gh_repo:
        return JsonResponse(
            {"error": "github_repo obligatorio si github_enabled=True"}, status=400,
        )
    if gh_enabled and gh_repo:
        if repo_has_bad_shape(gh_repo):
            return JsonResponse(
                {"error": "github_repo debe tener formato owner/repo"}, status=400,
            )
        from panel.core.services import github as gh_svc
        if not gh_svc.has_token():
            return JsonResponse(
                {"error": "github_enabled=True pero no hay token guardado. Ve a /github/ primero."},
                status=400,
            )
    # model_profile_id es OBLIGATORIO — Project.model_profile es FK non-null
    # (on_delete=PROTECT). Sin esto, Project.objects.create() revienta con
    # IntegrityError (500 feo). permission_policy sí es opcional (nullable).
    profile = body.get("model_profile_id")
    policy = body.get("permission_policy_id")
    topic_id = body.get("telegram_topic_id")
    if not profile:
        return JsonResponse(
            {"error": "model_profile_id es requerido — elige un modelo"}, status=400,
        )
    from panel.core.models import ModelProfile, PermissionPolicy
    try:
        model_profile = ModelProfile.objects.get(pk=profile)
    except ModelProfile.DoesNotExist:
        return JsonResponse({"error": "model_profile_id no existe"}, status=400)
    try:
        permission_policy = PermissionPolicy.objects.get(pk=policy) if policy else None
    except PermissionPolicy.DoesNotExist:
        return JsonResponse({"error": "permission_policy_id no existe"}, status=400)
    # path derivado (no del cliente, server-side para no exponer FS).
    from django.conf import settings
    project = Project.objects.create(
        name=name,
        slug=slug,
        path=f"{settings.PROJECTS_ROOT}/{slug}",
        model_profile=model_profile,
        permission_policy=permission_policy,
        github_repo=gh_repo,
        github_enabled=gh_enabled,
    )
    if topic_id is not None:
        try:
            project.telegram_topic_id = int(topic_id)
            project.save(update_fields=["telegram_topic_id", "updated_at"])
        except (TypeError, ValueError):
            pass
    # Provision + rollback ante fallo (D12 / FASE A.5).
    from panel.core.services import privileged
    from panel.core.services import provisioning as prov_svc
    try:
        prov_svc.provision_project(project)
    except privileged.ProvisioningError as exc:
        import shutil, os
        if os.path.isdir(project.path):
            shutil.rmtree(project.path, ignore_errors=True)
        Project.objects.filter(pk=project.pk).delete()
        return JsonResponse({"error": str(exc)}, status=400)
    except Exception as exc:
        import shutil, os
        if os.path.isdir(project.path):
            shutil.rmtree(project.path, ignore_errors=True)
        Project.objects.filter(pk=project.pk).delete()
        return JsonResponse({"error": f"provisioning falló: {exc}"}, status=400)
    project.refresh_from_db()
    warnings = []
    if project.github_warn_no_push:
        warnings.append(
            "Proyecto creado, pero el PAT actual no tiene push sobre este repo. "
            "`git push`/abrir PR fallarán hasta regenerar el token."
        )
    return JsonResponse({
        "ok": True,
        "slug": project.slug,
        "path": project.path,
        "warnings": warnings,
    }, status=201)


def repo_has_bad_shape(repo: str) -> bool:
    if repo.startswith(("http://", "https://", "git@")) or repo.endswith(".git"):
        return True
    if "/" not in repo or repo.count("/") > 1:
        return True
    return False


# ---- SP4: recreate desde archived/deleted ----

@csrf_exempt
@require_http_methods(["POST"])
@require_verified_json
def project_recreate(request: HttpRequest, slug: str) -> JsonResponse:
    """POST /api/v1/projects/<slug>/recreate/
    Hard-delea el Project archived/deleted existente (sesiones + dir) y crea
    uno NUEVO con mismo slug + FKs + github_repo. Re-clona el repo. 404 si
    no existe el slug. 409 si está active (no se puede recrear algo en uso).
    """
    from panel.core.services import provisioning as prov_svc
    from panel.core.services import privileged
    p = get_object_or_404(Project, slug=slug)
    if p.status == Project.Status.ACTIVE:
        return JsonResponse(
            {"error": f"proyecto '{slug}' está activo; archívalo primero"},
            status=409,
        )
    try:
        new = prov_svc.recreate_project(p)
    except privileged.ProvisioningError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except Exception as exc:
        return JsonResponse({"error": f"recreate falló: {exc}"}, status=400)
    return JsonResponse(
        {
            "ok": True,
            "slug": new.slug,
            "path": new.path,
            "recreated_from": str(p.id),
            "warnings": (
                ["El PAT actual no tiene push sobre este repo."]
                if new.github_warn_no_push else []
            ),
        },
        status=201,
    )
