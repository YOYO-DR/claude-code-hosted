"""Asignación de puertos (§4.5). Rango 20000-29999 (INFRA.md), 127.0.0.1. La
unicidad de `PortRegistry.port` + reintento garantizan cero duplicados bajo
concurrencia. La DB es la única fuente de verdad; si Postgres cae, el error
sube limpio al agente y no queda puerto fantasma (la fila solo existe si el
INSERT/UPDATE tuvo éxito)."""

from __future__ import annotations

import random
import re

from django.db import IntegrityError, transaction

from panel.core.models import PortRegistry, Project

PORT_MIN = 20000
PORT_MAX = 29999

# Detecta binds de puerto en comandos: `-p 8080`, `-p8080`, `-p 0.0.0.0:8080:80`,
# `--port 8080`, `--port=8080`. Captura el puerto del HOST (el que colisiona).
_PORT_BIND_RE = re.compile(
    r"(?:--port[=\s]+|(?<![\w-])-p[=\s]*)"
    r"(?:\d{1,3}(?:\.\d{1,3}){3}:)?"  # ip opcional
    r"(\d{2,5})"                       # puerto host
)


def bound_ports(command: str) -> list[int]:
    """Puertos que un comando intenta enlazar (heurística sobre -p/--port)."""
    return [int(m) for m in _PORT_BIND_RE.findall(command or "")]


class PortError(RuntimeError):
    pass


def allocate(slug: str, purpose: str, session_id: str | None = None) -> int:
    """Reserva un puerto libre para el proyecto `slug`. Reutiliza filas
    `released` o crea nuevas; ambas protegidas por la unique de `port`."""
    project = Project.objects.get(slug=slug)
    active = set(
        PortRegistry.objects.filter(status=PortRegistry.Status.ACTIVE).values_list(
            "port", flat=True
        )
    )
    candidates = [p for p in range(PORT_MIN, PORT_MAX + 1) if p not in active]
    if not candidates:
        raise PortError("no hay puertos libres en el rango 20000-29999")
    random.shuffle(candidates)
    for port in candidates[:200]:
        try:
            with transaction.atomic():
                row = PortRegistry.objects.filter(port=port).first()
                if row is None:
                    PortRegistry.objects.create(
                        port=port, project=project, purpose=purpose,
                        status=PortRegistry.Status.ACTIVE, allocated_by_session=session_id,
                    )
                elif row.status == PortRegistry.Status.RELEASED:
                    # reactivar SOLO si sigue released (condición atómica)
                    updated = PortRegistry.objects.filter(
                        port=port, status=PortRegistry.Status.RELEASED
                    ).update(
                        project=project, purpose=purpose,
                        status=PortRegistry.Status.ACTIVE, allocated_by_session=session_id,
                    )
                    if updated == 0:
                        continue  # otro lo reactivó primero
                else:
                    continue  # activo: tomado
            return port
        except IntegrityError:
            continue  # carrera en el INSERT: otro tomó este puerto
    raise PortError("no se pudo asignar un puerto (contención alta); reintenta")


def list_ports() -> list[dict]:
    rows = (
        PortRegistry.objects.filter(status=PortRegistry.Status.ACTIVE)
        .select_related("project")
        .order_by("port")
    )
    return [
        {"port": r.port, "project": r.project.slug, "purpose": r.purpose, "status": r.status}
        for r in rows
    ]


def release(slug: str, port: int) -> bool:
    """Libera un puerto SOLO si pertenece al proyecto `slug` y está activo."""
    updated = PortRegistry.objects.filter(
        port=port, status=PortRegistry.Status.ACTIVE, project__slug=slug
    ).update(status=PortRegistry.Status.RELEASED, allocated_by_session=None)
    return updated > 0


def ports_of(slug: str) -> set[int]:
    """Puertos activos de un proyecto (para el hook de reescritura)."""
    return set(
        PortRegistry.objects.filter(
            status=PortRegistry.Status.ACTIVE, project__slug=slug
        ).values_list("port", flat=True)
    )


def owner_of(port: int) -> str | None:
    row = (
        PortRegistry.objects.filter(port=port, status=PortRegistry.Status.ACTIVE)
        .select_related("project")
        .first()
    )
    return row.project.slug if row else None


def guard_command(slug: str, command: str) -> tuple[str, str | None]:
    """Hook de puertos (§4.2). Si el comando enlaza un puerto ACTIVO de OTRO
    proyecto: reescribe al puerto asignado de este proyecto si tiene exactamente
    uno, o deniega. Devuelve (action, payload):
      ("ok", None) | ("rewrite", nuevo_comando) | ("deny", mensaje)."""
    conflicts = []
    for port in bound_ports(command):
        owner = owner_of(port)
        if owner is not None and owner != slug:
            conflicts.append((port, owner))
    if not conflicts:
        return ("ok", None)

    mine = sorted(ports_of(slug))
    detail = ", ".join(f"{p} (de '{o}')" for p, o in conflicts)
    if len(mine) == 1:
        new_cmd = command
        for port, _ in conflicts:
            new_cmd = re.sub(rf"(?<!\d){port}(?!\d)", str(mine[0]), new_cmd)
        return ("rewrite", new_cmd)
    return (
        "deny",
        f"El/los puerto(s) {detail} pertenecen a otro proyecto. No los uses: "
        f"llama a la herramienta allocate_port para obtener un puerto propio y "
        f"enlaza ese.",
    )
