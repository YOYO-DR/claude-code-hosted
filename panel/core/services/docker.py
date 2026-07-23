"""SP15 — vista de contenedores Docker.

Motivación: al parar una sesión de un proyecto, los contenedores que el agente
levantó siguen corriendo. Esta vista los lista y permite pararlos.

Solo `stop`: SIGTERM + timeout, sin borrar el contenedor ni sus volúmenes (nada
de `rm`, `down` ni `-v`). Lo parado se puede volver a arrancar con sus datos
intactos.

El panel corre como `panel`, que NO está en el grupo docker (estarlo equivale a
root). Todo pasa por `sudo -n /opt/panel/deploy/panel-docker.sh`, whitelisteado
en sudoers y limitado a `list`/`stop`.

Los contenedores de la propia plataforma (compose `panel-infra`: postgres,
redis, traefik) se **ocultan** de la vista: no son del operador y pararlos
tumbaría el panel. El helper además rechaza pararlos aunque llegue la orden.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

DOCKER_HELPER = "/opt/panel/deploy/panel-docker.sh"

# Compose de la infra del panel. Se oculta de la vista y no se puede parar.
PROTECTED_PROJECT = os.environ.get("PANEL_INFRA_PROJECT", "panel-infra")

# Nombres de contenedor sueltos (sin compose) que también son del panel.
# Se comparan en minúsculas contra el nombre exacto.
PROTECTED_NAMES = {"panel", "panel-infra", "traefik"}

# Mismo charset que valida el helper: IDs hex y nombres de Docker.
VALID_REF = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")

STOP_TIMEOUT = 40  # s; el helper usa 15s por contenedor, damos margen


class DockerError(RuntimeError):
    """Fallo al hablar con Docker. `code` distingue: 2=input inválido,
    3=protegido, 127=docker ausente, otro=error del daemon."""

    def __init__(self, message: str, code: int = 0) -> None:
        super().__init__(message)
        self.code = code


def _is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def _run(args: list[str]) -> subprocess.CompletedProcess:
    """Ejecuta el helper. Como root corre docker directo (dev/tests); como
    `panel` va por sudo. Mismo patrón que privileged.py."""
    if not _is_root() and shutil.which("sudo") and os.path.exists(DOCKER_HELPER):
        cmd = ["sudo", "-n", DOCKER_HELPER, *args]
    elif os.path.exists(DOCKER_HELPER):
        cmd = [DOCKER_HELPER, *args]
    else:
        # Sin helper (dev local): hablamos con docker directamente para que la
        # vista sea usable fuera del VPS.
        cmd = _direct_docker_cmd(args)
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=STOP_TIMEOUT)
    except FileNotFoundError as exc:
        raise DockerError("docker no está instalado o no es accesible", 127) from exc
    except subprocess.TimeoutExpired as exc:
        raise DockerError("docker no respondió a tiempo", 124) from exc


def _direct_docker_cmd(args: list[str]) -> list[str]:
    """Equivalente del helper sin sudo, para desarrollo local."""
    if args[0] == "list":
        return [
            "docker", "ps", "-a", "--no-trunc", "--format",
            '{"id":"{{.ID}}","name":"{{.Names}}","state":"{{.State}}",'
            '"status":"{{.Status}}","image":"{{.Image}}",'
            '"project":"{{.Label "com.docker.compose.project"}}",'
            '"service":"{{.Label "com.docker.compose.service"}}",'
            '"ports":"{{.Ports}}","created":"{{.CreatedAt}}"}',
        ]
    if args[0] == "stop":
        return ["docker", "stop", "--time", "15", args[1]]
    raise DockerError(f"subcomando desconocido: {args[0]}", 2)


def _is_panel_container(row: dict) -> bool:
    """True si el contenedor es de la plataforma (se oculta de la vista)."""
    if (row.get("project") or "") == PROTECTED_PROJECT:
        return True
    return (row.get("name") or "").lower() in PROTECTED_NAMES


def list_containers() -> dict:
    """Devuelve {"groups": [...], "standalone": [...]}.

    `groups` son proyectos de docker compose (label
    `com.docker.compose.project`) con sus contenedores dentro; `standalone` son
    contenedores sin compose. Los del panel se filtran fuera de ambos.
    """
    proc = _run(["list"])
    if proc.returncode != 0:
        raise DockerError(
            (proc.stderr or "docker ps falló").strip()[:300], proc.returncode
        )
    rows: list[dict] = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            # Una línea corrupta (p.ej. un nombre con comillas) no debe tumbar
            # la vista entera.
            continue
        if not isinstance(row, dict) or not row.get("id"):
            continue
        if _is_panel_container(row):
            continue
        rows.append(_normalize(row))

    groups: dict[str, list[dict]] = {}
    standalone: list[dict] = []
    for row in rows:
        proj = row["project"]
        if proj:
            groups.setdefault(proj, []).append(row)
        else:
            standalone.append(row)

    out_groups = []
    for name, containers in sorted(groups.items()):
        containers.sort(key=lambda c: (c["service"] or "", c["name"]))
        running = sum(1 for c in containers if c["running"])
        out_groups.append({
            "project": name,
            "containers": containers,
            "total": len(containers),
            "running": running,
        })
    # Los grupos con algo corriendo primero — son los que el operador busca.
    out_groups.sort(key=lambda g: (-g["running"], g["project"]))
    standalone.sort(key=lambda c: (not c["running"], c["name"]))
    return {"groups": out_groups, "standalone": standalone}


def _normalize(row: dict) -> dict:
    state = str(row.get("state") or "").lower()
    return {
        "id": str(row.get("id") or "")[:12],
        "full_id": str(row.get("id") or ""),
        "name": str(row.get("name") or ""),
        "state": state,
        "running": state == "running",
        "status": str(row.get("status") or ""),
        "image": str(row.get("image") or ""),
        "project": str(row.get("project") or ""),
        "service": str(row.get("service") or ""),
        "ports": str(row.get("ports") or ""),
    }


def stop_container(ref: str) -> dict:
    """Para UN contenedor por id o nombre. Idempotente desde el punto de vista
    del operador: parar algo ya parado devuelve ok."""
    ref = (ref or "").strip()
    if not VALID_REF.match(ref):
        raise DockerError(f"identificador inválido: {ref!r}", 2)
    proc = _run(["stop", ref])
    if proc.returncode == 3:
        raise DockerError("ese contenedor es de la infraestructura del panel", 3)
    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        # docker stop de algo ya parado devuelve 0; un 'No such container'
        # significa que desapareció entre el list y el stop — no es un error
        # accionable para el operador.
        if "No such container" in err:
            return {"stopped": ref, "already_gone": True}
        raise DockerError(err[:300] or "docker stop falló", proc.returncode)
    return {"stopped": ref}


def stop_project(project: str) -> dict:
    """Para todos los contenedores de un proyecto compose, uno por uno.

    Deliberadamente NO usa `docker compose stop`: no necesitamos el fichero
    compose (que vive en el workspace del agente y puede no existir), y así el
    conjunto de contenedores a parar sale del daemon, no de un YAML que podría
    haber cambiado. Sigue siendo `stop` — no borra nada.
    """
    project = (project or "").strip()
    if not VALID_REF.match(project):
        raise DockerError(f"proyecto inválido: {project!r}", 2)
    if project == PROTECTED_PROJECT:
        raise DockerError("ese proyecto es la infraestructura del panel", 3)
    data = list_containers()
    group = next((g for g in data["groups"] if g["project"] == project), None)
    if group is None:
        raise DockerError(f"proyecto no encontrado: {project}", 404)
    stopped, errors = [], []
    for c in group["containers"]:
        if not c["running"]:
            continue
        try:
            stop_container(c["full_id"] or c["id"])
            stopped.append(c["name"])
        except DockerError as exc:
            errors.append({"name": c["name"], "error": str(exc)})
    return {"project": project, "stopped": stopped, "errors": errors}
