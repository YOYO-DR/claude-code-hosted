"""Operaciones de render/provisioning que necesitan root (leer panel.env, chown
a `agents`, escribir en dirs ajenos). El panel corre como usuario `panel` sin
privilegios; delega en helpers vía `sudo -n` (sudoers restringido), igual que
supervisor.py con systemctl.

Estrategia (misma que supervisor._systemctl):
- root  -> ejecuta el render en proceso.
- panel + sudo + helper presente -> sudo al helper (corre como root).
- local/tests (sin helper) -> ejecuta en proceso (sin chown; no hace falta).
"""

from __future__ import annotations

import os
import shutil
import subprocess

RENDER_HELPER = "/opt/panel/deploy/panel-render.sh"
PROVISION_HELPER = "/opt/panel/deploy/panel-provision.sh"
CLONE_HELPER = "/opt/panel/deploy/panel-clone.sh"


class ProvisioningError(RuntimeError):
    """Fallo del provisioning clonado desde GitHub (D12). Distinguible del
    `CalledProcessError` genérico: lleva `repo`, `branch`, `stderr` y `code`
    para que la vista pueda devolver un mensaje útil al operador (en vez de
    propagar la excepción como 502)."""

    def __init__(self, message: str, *, repo: str = "", branch: str = "",
                 stderr: str = "", code: int = 0) -> None:
        super().__init__(message)
        self.repo = repo
        self.branch = branch
        self.stderr = stderr
        self.code = code


def _is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def _can_sudo(helper: str) -> bool:
    return shutil.which("sudo") is not None and os.path.exists(helper)


def _friendly_clone_message(stderr: str) -> str:
    """Traduce el stderr de git a un mensaje actionable para el operador."""
    s = (stderr or "").lower()
    if "could not read username" in s or "terminal prompts disabled" in s:
        return (
            "GitHub no autenticó el clone: el token del panel no tiene acceso "
            "al repositorio (o el token no está configurado). Pega un PAT con "
            "scope `repo` en /github/ y vuelve a intentarlo."
        )
    if "remote: repository not found" in s or "not found" in s:
        return (
            "GitHub devolvió 404: el repositorio no existe, es privado y tu "
            "PAT no tiene acceso, o el nombre está mal escrito. Verifica en "
            "https://github.com/<owner>/<repo>."
        )
    # 403 genérico de git cuando GitHub deniega (incluye "Write access to
    # repository not granted" + "The requested URL returned error: 403").
    if "403" in s or "permission denied" in s or "write access to repository" in s:
        return (
            "GitHub denegó el acceso (403): tu PAT no tiene permisos sobre "
            "este repositorio. Regenera el token con scope `repo` sobre el "
            "repo correcto y pégalo en /github/."
        )
    if "could not resolve host" in s or "network is unreachable" in s:
        return "Sin red: no se pudo contactar github.com. Revisa DNS/red."
    return (stderr or "git clone falló sin stderr").strip().splitlines()[-1][:300]


def run_render() -> None:
    if not _is_root() and _can_sudo(RENDER_HELPER):
        subprocess.run(["sudo", "-n", RENDER_HELPER], check=True)
        return
    from panel.core import renderer

    renderer.render_all()


def run_provision(slug: str, path: str) -> None:
    if not _is_root() and _can_sudo(PROVISION_HELPER):
        subprocess.run(["sudo", "-n", PROVISION_HELPER, "provision", slug, path], check=True)
        return
    _provision_inprocess(path)


def write_agents_md(path: str, content: str) -> None:
    """Escribe AGENTS.md en `path` (bajo /srv/projects/) como root. El panel lo
    genera en Python y lo manda por stdin al helper sudo. Idempotente."""
    if not _is_root() and _can_sudo(PROVISION_HELPER):
        subprocess.run(
            ["sudo", "-n", PROVISION_HELPER, "write-agents", path],
            input=content, text=True, check=True,
        )
        return
    _write_agents_md_inprocess(path, content)


def remove_agents_md(path: str) -> None:
    """Borra AGENTS.md de `path` si existe. Idempotente (no falla si no está)."""
    if not _is_root() and _can_sudo(PROVISION_HELPER):
        subprocess.run(
            ["sudo", "-n", PROVISION_HELPER, "remove-agents", path], check=True,
        )
        return
    from pathlib import Path
    Path(path, "AGENTS.md").unlink(missing_ok=True)


def run_clone(path: str, repo: str, branch: str, token: str) -> None:
    """Clona `repo` en `path`, crea `branch`, chownea a agents y renderiza. El
    token va por STDIN (nunca argv/disco).

    Si el clone falla (red, 403, 404), levanta `ProvisioningError` con el
    mensaje legible (D12). La vista captura esta excepción y la traduce a 400
    con rollback del proyecto a medias.
    """
    if not _is_root() and _can_sudo(CLONE_HELPER):
        proc = subprocess.run(
            ["sudo", "-n", CLONE_HELPER, path, repo, branch],
            input=token + "\n", text=True,
            capture_output=True,
        )
        if proc.returncode != 0:
            raise ProvisioningError(
                _friendly_clone_message(proc.stderr),
                repo=repo, branch=branch,
                stderr=proc.stderr, code=proc.returncode,
            )
        return
    _clone_inprocess(path, repo, branch, token)


def _clone_inprocess(path: str, repo: str, branch: str, token: str) -> None:
    """Clone + rama + render en proceso (root/tests), sin chown."""
    import shutil

    from panel.core import renderer
    from panel.core.services import github

    shutil.rmtree(path, ignore_errors=True)
    github.clone(token, repo, path)
    github.create_branch(path, branch)
    renderer.render_all()


def _provision_inprocess(path: str) -> None:
    """mkdir + git init + render, sin chown (root/tests). En prod el chown a
    `agents` lo hace el helper."""
    import subprocess as sp
    from pathlib import Path

    from panel.core import renderer

    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    if not (p / ".git").exists():
        sp.run(["git", "init", "-q", str(p)], check=True)
    renderer.render_all()


def _write_agents_md_inprocess(path: str, content: str) -> None:
    """Escribe AGENTS.md en proceso (root/tests), sin chown."""
    from pathlib import Path
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    (p / "AGENTS.md").write_text(content, encoding="utf-8")
