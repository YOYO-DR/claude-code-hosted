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


def _is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def _can_sudo(helper: str) -> bool:
    return shutil.which("sudo") is not None and os.path.exists(helper)


def run_render() -> None:
    if not _is_root() and _can_sudo(RENDER_HELPER):
        subprocess.run(["sudo", "-n", RENDER_HELPER], check=True)
        return
    from panel.core import renderer

    renderer.render_all()


def run_provision(slug: str, path: str) -> None:
    if not _is_root() and _can_sudo(PROVISION_HELPER):
        subprocess.run(["sudo", "-n", PROVISION_HELPER, slug, path], check=True)
        return
    _provision_inprocess(path)


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
