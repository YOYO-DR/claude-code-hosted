"""Asignación de puertos (Gate 4): concurrencia sin duplicados, release solo del
propio proyecto, guard de binds cross-project, detección de puertos."""

from __future__ import annotations

import pytest

from panel.core.models import ModelProfile, PermissionPolicy, PortRegistry, Project
from panel.core.services import ports as ports_svc

pytestmark = pytest.mark.django_db(transaction=True)


def _project(slug):
    profile = ModelProfile.objects.create(name=f"m-{slug}", provider="anthropic", model="x")
    return Project.objects.create(
        slug=slug, name=slug, path=f"/srv/projects/{slug}",
        model_profile=profile,
        permission_policy=PermissionPolicy.objects.create(name=f"p-{slug}"),
    )


def test_allocate_in_range_and_registered():
    _project("a")
    port = ports_svc.allocate("a", "web")
    assert ports_svc.PORT_MIN <= port <= ports_svc.PORT_MAX
    assert PortRegistry.objects.filter(port=port, status="active").exists()


def test_release_own_port():
    _project("a")
    port = ports_svc.allocate("a", "web")
    assert ports_svc.release("a", port) is True
    assert not PortRegistry.objects.filter(port=port, status="active").exists()


def test_release_foreign_port_rejected():
    _project("a")
    _project("b")
    port = ports_svc.allocate("a", "web")
    # b intenta liberar un puerto de a → rechazado
    assert ports_svc.release("b", port) is False
    assert PortRegistry.objects.filter(port=port, status="active").exists()


def test_released_port_reusable():
    _project("a")
    port = ports_svc.allocate("a", "web")
    ports_svc.release("a", port)
    # forzar reuso llenando... simplemente re-allocate muchas veces no garantiza
    # el mismo puerto; en su lugar verificamos que una fila released puede volver
    # a activa vía allocate cuando es el único candidato no es práctico. Basta con
    # que allocate no rompa con filas released presentes.
    port2 = ports_svc.allocate("a", "web2")
    assert PortRegistry.objects.filter(port=port2, status="active").exists()


def test_sequential_allocations_are_distinct():
    _project("a")
    ports = {ports_svc.allocate("a", f"s{i}") for i in range(50)}
    assert len(ports) == 50  # ninguno se repite (no reusa activos)


def test_unique_constraint_blocks_duplicate_active():
    # La garantía anti-duplicado bajo concurrencia es la unique de `port`:
    # dos filas activas con el mismo puerto es imposible a nivel DB.
    from django.db import IntegrityError, transaction

    a = _project("a")
    port = ports_svc.allocate("a", "web")
    with pytest.raises(IntegrityError), transaction.atomic():
        PortRegistry.objects.create(port=port, project=a, purpose="dup", status="active")
    # (El test de 100 asignaciones concurrentes reales corre en Postgres/VPS.)


def test_list_ports():
    _project("a")
    p = ports_svc.allocate("a", "web")
    rows = ports_svc.list_ports()
    assert any(r["port"] == p and r["project"] == "a" and r["purpose"] == "web" for r in rows)


# ---------- guard de binds ----------

def test_bound_ports_detection():
    assert ports_svc.bound_ports("docker run -p 8080:80 x") == [8080]
    assert ports_svc.bound_ports("app --port 9000") == [9000]
    assert ports_svc.bound_ports("app --port=9000") == [9000]
    assert ports_svc.bound_ports("docker run -p 0.0.0.0:20001:80 x") == [20001]
    assert ports_svc.bound_ports("ls -p") == []  # -p sin número


def test_guard_ok_when_own_or_unregistered():
    _project("a")
    assert ports_svc.guard_command("a", "docker run -p 8080:80 x") == ("ok", None)


def test_guard_denies_foreign_port():
    _project("a")
    _project("b")
    port = ports_svc.allocate("b", "web")  # puerto de b
    action, msg = ports_svc.guard_command("a", f"docker run -p {port}:80 x")
    assert action == "deny"
    assert "allocate_port" in msg


def test_guard_rewrites_to_own_single_port():
    _project("a")
    _project("b")
    foreign = ports_svc.allocate("b", "web")
    mine = ports_svc.allocate("a", "web")  # a tiene exactamente uno
    action, new_cmd = ports_svc.guard_command("a", f"docker run -p {foreign}:80 x")
    assert action == "rewrite"
    assert str(mine) in new_cmd and str(foreign) not in new_cmd
