"""SP15 — vista de contenedores Docker: agrupación por compose, ocultado de la
infra del panel, validación de identificadores y que solo se hace `stop`."""

from __future__ import annotations

import subprocess

import pytest

from panel.core.services import docker as dk

# Salida real del helper (una línea JSON por contenedor), tomada del VPS.
SAMPLE = "\n".join([
    '{"id":"aaa1","name":"yodumanager_local_django","state":"running","status":"Up 2 hours","image":"yodumanager_local_django","project":"yodumanager-v2","service":"django","ports":"0.0.0.0:8000->8000/tcp","created":"x"}',
    '{"id":"aaa2","name":"yodumanager_local_postgres","state":"running","status":"Up 2 hours","image":"postgres:16","project":"yodumanager-v2","service":"postgres","ports":"","created":"x"}',
    '{"id":"bbb1","name":"plantilla_django_react_local_redis","state":"exited","status":"Exited (0) 3 minutes ago","image":"redis:7.2","project":"plantilla-django-react","service":"redis","ports":"","created":"x"}',
    '{"id":"ccc1","name":"suelto-nginx","state":"running","status":"Up 5 days","image":"nginx","project":"","service":"","ports":"80/tcp","created":"x"}',
    # Infra del panel — debe quedar OCULTA.
    '{"id":"ddd1","name":"panel-infra-postgres-1","state":"running","status":"Up 4 days","image":"postgres:16","project":"panel-infra","service":"postgres","ports":"","created":"x"}',
    '{"id":"ddd2","name":"panel-infra-traefik-1","state":"running","status":"Up 4 days","image":"traefik:v3.3","project":"panel-infra","service":"traefik","ports":"","created":"x"}',
])


class FakeProc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


@pytest.fixture
def fake_list(monkeypatch):
    monkeypatch.setattr(dk, "_run", lambda args: FakeProc(stdout=SAMPLE))


# ---------- listado y agrupación ----------

def test_groups_by_compose_project(fake_list):
    data = dk.list_containers()
    names = [g["project"] for g in data["groups"]]
    assert "yodumanager-v2" in names
    assert "plantilla-django-react" in names
    grupo = next(g for g in data["groups"] if g["project"] == "yodumanager-v2")
    assert grupo["total"] == 2
    assert grupo["running"] == 2


def test_standalone_containers_are_separate(fake_list):
    data = dk.list_containers()
    assert [c["name"] for c in data["standalone"]] == ["suelto-nginx"]


def test_panel_infra_is_hidden(fake_list):
    """Los contenedores de la plataforma no se muestran: no son del operador y
    pararlos tumbaría el panel."""
    data = dk.list_containers()
    assert all(g["project"] != "panel-infra" for g in data["groups"])
    todos = [c["name"] for g in data["groups"] for c in g["containers"]]
    todos += [c["name"] for c in data["standalone"]]
    assert not any("panel-infra" in n for n in todos)


def test_running_groups_sort_first(fake_list):
    data = dk.list_containers()
    # yodumanager (2 corriendo) antes que plantilla (0 corriendo).
    assert data["groups"][0]["project"] == "yodumanager-v2"


def test_normalize_marks_running(fake_list):
    data = dk.list_containers()
    grupo = next(g for g in data["groups"] if g["project"] == "plantilla-django-react")
    assert grupo["containers"][0]["running"] is False
    assert grupo["running"] == 0


def test_corrupt_line_does_not_break_listing(monkeypatch):
    monkeypatch.setattr(
        dk, "_run",
        lambda args: FakeProc(stdout=SAMPLE + "\n{roto sin json\n" + "\n"),
    )
    data = dk.list_containers()
    assert len(data["groups"]) == 2  # sigue funcionando


def test_list_raises_on_docker_error(monkeypatch):
    monkeypatch.setattr(
        dk, "_run", lambda args: FakeProc(stderr="Cannot connect", returncode=1)
    )
    with pytest.raises(dk.DockerError) as e:
        dk.list_containers()
    assert "Cannot connect" in str(e.value)


# ---------- validación de identificadores ----------

@pytest.mark.parametrize("bad", [
    "", "  ", "a b", "a;rm -rf /", "$(id)", "`id`", "a|b", "-flag", "../x", "a\nb",
])
def test_stop_rejects_invalid_refs(bad):
    with pytest.raises(dk.DockerError) as e:
        dk.stop_container(bad)
    assert e.value.code == 2


@pytest.mark.parametrize("good", ["aaa1", "my_container", "my-container.1", "abc123DEF"])
def test_stop_accepts_valid_refs(monkeypatch, good):
    monkeypatch.setattr(dk, "_run", lambda args: FakeProc())
    assert dk.stop_container(good) == {"stopped": good}


def test_stop_project_rejects_invalid_name():
    with pytest.raises(dk.DockerError) as e:
        dk.stop_project("bad name;")
    assert e.value.code == 2


# ---------- protección de la infra del panel ----------

def test_stop_project_refuses_panel_infra():
    with pytest.raises(dk.DockerError) as e:
        dk.stop_project("panel-infra")
    assert e.value.code == 3


def test_stop_container_propagates_helper_rejection(monkeypatch):
    """El helper devuelve 3 si el contenedor es de panel-infra (defensa en
    profundidad: aunque se colara el id, el helper lo rechaza)."""
    monkeypatch.setattr(dk, "_run", lambda args: FakeProc(returncode=3, stderr="rechazado"))
    with pytest.raises(dk.DockerError) as e:
        dk.stop_container("ddd1")
    assert e.value.code == 3
    assert "infraestructura del panel" in str(e.value)


# ---------- stop de un proyecto ----------

def test_stop_project_stops_only_running(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(args):
        calls.append(list(args))
        return FakeProc(stdout=SAMPLE) if args[0] == "list" else FakeProc()

    monkeypatch.setattr(dk, "_run", fake_run)
    out = dk.stop_project("yodumanager-v2")
    assert set(out["stopped"]) == {"yodumanager_local_django", "yodumanager_local_postgres"}
    assert out["errors"] == []
    # Solo `stop` — nunca rm/down/-v.
    stops = [c for c in calls if c[0] == "stop"]
    assert len(stops) == 2
    assert all(c[0] == "stop" for c in stops)
    assert not any(x in ("rm", "down", "-v", "--volumes") for c in calls for x in c)


def test_stop_project_skips_already_stopped(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(args):
        calls.append(list(args))
        return FakeProc(stdout=SAMPLE) if args[0] == "list" else FakeProc()

    monkeypatch.setattr(dk, "_run", fake_run)
    out = dk.stop_project("plantilla-django-react")
    assert out["stopped"] == []          # su único contenedor estaba exited
    assert [c for c in calls if c[0] == "stop"] == []


def test_stop_project_unknown(fake_list):
    with pytest.raises(dk.DockerError) as e:
        dk.stop_project("no-existe")
    assert e.value.code == 404


def test_stop_project_collects_errors(monkeypatch):
    def fake_run(args):
        if args[0] == "list":
            return FakeProc(stdout=SAMPLE)
        if args[1] == "aaa1":
            return FakeProc(returncode=1, stderr="boom")
        return FakeProc()

    monkeypatch.setattr(dk, "_run", fake_run)
    out = dk.stop_project("yodumanager-v2")
    assert out["stopped"] == ["yodumanager_local_postgres"]
    assert len(out["errors"]) == 1
    assert "boom" in out["errors"][0]["error"]


def test_stop_container_tolerates_vanished(monkeypatch):
    """Si el contenedor desaparece entre el list y el stop, no es un error
    accionable para el operador."""
    monkeypatch.setattr(
        dk, "_run",
        lambda args: FakeProc(returncode=1, stderr="Error: No such container: aaa1"),
    )
    assert dk.stop_container("aaa1") == {"stopped": "aaa1", "already_gone": True}


# ---------- docker ausente ----------

def test_docker_missing_is_typed(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError()

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(dk.os.path, "exists", lambda p: False)
    monkeypatch.setattr(dk.shutil, "which", lambda p: None)
    with pytest.raises(dk.DockerError) as e:
        dk.list_containers()
    assert e.value.code == 127


# ---------- el helper solo expone list/stop ----------

def test_helper_script_has_no_destructive_commands():
    from pathlib import Path

    script = Path(__file__).resolve().parents[2] / "deploy" / "panel-docker.sh"
    body = script.read_text()
    assert "docker stop" in body
    for forbidden in ("docker rm", "docker compose down", "down -v", "--volumes", "docker system prune"):
        assert forbidden not in body, forbidden
