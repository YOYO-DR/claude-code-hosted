"""Arranque/parada de units de sesión vía systemctl (§ Fase 1.4). El usuario
`panel` tiene sudoers restringido SOLO a
`systemctl {start,stop,status} claude-session@*`. El SESSION_ID viaja por el
nombre de instancia (%i), así que no hace falta archivo de env por sesión."""

from __future__ import annotations

import shutil
import subprocess

UNIT_PREFIX = "claude-session@"


class SupervisorError(RuntimeError):
    pass


def _systemctl() -> list[str]:
    # sudo solo si no somos root (en tests/local puede no haber sudo).
    base = ["systemctl"]
    if shutil.which("sudo"):
        return ["sudo", "-n", *base]
    return base


def _run(action: str, sid: str) -> subprocess.CompletedProcess[str]:
    if action not in {"start", "stop", "status"}:
        raise ValueError(f"acción no permitida: {action}")
    unit = f"{UNIT_PREFIX}{sid}.service"
    return subprocess.run(
        [*_systemctl(), action, unit],
        capture_output=True,
        text=True,
        check=False,
    )


def start(sid: str) -> None:
    res = _run("start", sid)
    if res.returncode != 0:
        raise SupervisorError(f"start {sid} falló: {res.stderr.strip()}")


def stop(sid: str) -> None:
    res = _run("stop", sid)
    if res.returncode != 0:
        raise SupervisorError(f"stop {sid} falló: {res.stderr.strip()}")


def is_active(sid: str) -> bool:
    res = _run("status", sid)
    return res.returncode == 0
