"""Permisos mixtos y aprobaciones (Gate 3): preview, timeout config, espera de
respuesta, timeout instructivo, carrera de respuestas (threads reales),
allow_always persiste regla + render, expire_pending, rewrite. Los paths allow/
deny/allow_always completos con worker+SDK se verifican e2e en el VPS."""

from __future__ import annotations

import threading

import fakeredis
import fakeredis.aioredis
import pytest

from panel.core import bus
from panel.core.models import (
    ModelProfile,
    PermissionPolicy,
    PermissionRequest,
    Project,
    Session,
)
from panel.core.services import permissions as perm_svc
from panel.core.services import rewrite

# transaction=True: los tests async crean filas vía sync_to_async (otra conexión
# que commitea, fuera del rollback normal); truncar entre tests evita fugas.
pytestmark = pytest.mark.django_db(transaction=True)


_counter = 0


def _session(mode=PermissionPolicy.Mode.APPROVE, timeout=None):
    global _counter
    _counter += 1
    n = _counter
    profile = ModelProfile.objects.create(
        name=f"p{n}", provider=ModelProfile.Provider.ANTHROPIC, model="m"
    )
    policy = PermissionPolicy.objects.create(name=f"pol{n}", mode=mode)
    project = Project.objects.create(
        slug=f"demo{n}", name="Demo", path=f"/srv/projects/demo{n}",
        model_profile=profile, permission_policy=policy,
        permission_timeout_seconds=timeout,
    )
    return Session.objects.create(project=project)


def _aredis():
    return fakeredis.aioredis.FakeRedis(server=fakeredis.FakeServer())


# ---------- preview / timeout config ----------

def test_preview_truncates_to_500():
    assert len(perm_svc.make_preview("Bash", {"command": "x" * 1000})) == 500


def test_timeout_uses_project_then_default(settings):
    settings.PERMISSION_TIMEOUT_SECONDS = 900
    assert perm_svc.timeout_seconds(_session()) == 900
    s = _session(timeout=60)
    assert perm_svc.timeout_seconds(s) == 60


# ---------- espera de respuesta ----------

async def test_wait_answer_returns_seeded():
    ar = _aredis()
    await ar.set(bus.key_answer("req-1"), "allow")
    got = await perm_svc._wait_answer(ar, "req-1", 5, 0.02)
    assert got == "allow"


async def test_wait_answer_timeout_returns_none():
    ar = _aredis()
    got = await perm_svc._wait_answer(ar, "req-x", 1, 0.05)
    assert got is None


async def test_wait_answer_monotonic_ignores_wall_clock(monkeypatch):
    """Desfase de reloj (§6.4): el deadline usa time.monotonic, así que un salto
    del reloj de pared NO expira el permiso antes de tiempo."""
    import time

    ar = _aredis()
    await ar.set(bus.key_answer("req-1"), "allow|web")
    # simular reloj de pared saltado +1 año; monotonic sigue normal
    monkeypatch.setattr(time, "time", lambda: time.monotonic() + 31_536_000)
    got = await perm_svc._wait_answer(ar, "req-1", timeout_s=5, poll_interval=0.02)
    assert got == "allow|web"  # devuelve la respuesta, no timeout


async def test_request_and_wait_timeout_expires(monkeypatch):
    from asgiref.sync import sync_to_async

    session = await sync_to_async(_session)()
    monkeypatch.setattr(perm_svc, "timeout_seconds", lambda s: 1)
    ar = _aredis()
    answer, _e, _c, req = await perm_svc.request_and_wait(
        session, "Bash", {"command": "sleep"}, aredis=ar, hooks=[], poll_interval=0.05
    )
    assert answer == "timeout"
    await sync_to_async(req.refresh_from_db)()
    assert req.status == PermissionRequest.Status.EXPIRED
    assert req.resolved_by == PermissionRequest.ResolvedBy.TIMEOUT


async def test_request_and_wait_allow_publishes_and_resolves(monkeypatch):
    """Path allow completo: publica en :perm, y al sembrar la respuesta del id
    publicado, resuelve como allowed."""
    import asyncio
    import json

    from asgiref.sync import sync_to_async

    session = await sync_to_async(_session)()
    monkeypatch.setattr(perm_svc, "timeout_seconds", lambda s: 3)
    ar = _aredis()
    pubsub = ar.pubsub()
    await pubsub.subscribe(bus.key_perm(str(session.id)))

    task = asyncio.create_task(
        perm_svc.request_and_wait(
            session, "Bash", {"command": "ls"}, aredis=ar, hooks=[], poll_interval=0.02
        )
    )
    data = None
    for _ in range(200):
        msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.02)
        if msg:
            data = json.loads(msg["data"])
            break
    assert data is not None and data["tool"] == "Bash"
    await ar.set(bus.key_answer(data["id"]), "allow")
    answer, _e, _c, req = await task
    assert answer == "allow"
    await sync_to_async(req.refresh_from_db)()
    assert req.status == PermissionRequest.Status.ALLOWED


# ---------- allow_always ----------

def test_allow_always_persists_rule_and_renders(monkeypatch):
    from panel.core.services import privileged

    calls = []
    monkeypatch.setattr(privileged, "run_render", lambda: calls.append(1))
    session = _session()
    req = perm_svc.create_request(session, "Bash(git push:*)", {"command": "git push"}, 900)
    perm_svc.apply_answer(req, "allow_always")
    req.refresh_from_db()
    assert req.status == PermissionRequest.Status.ALLOWED_ALWAYS
    policy = Project.objects.get(pk=session.project_id).permission_policy
    assert "Bash(git push:*)" in policy.allowed_tools
    assert calls == [1]


def test_apply_answer_source_telegram(monkeypatch):
    from panel.core.services import privileged

    monkeypatch.setattr(privileged, "run_render", lambda: None)
    session = _session()
    req = perm_svc.create_request(session, "Bash", {}, 900)
    perm_svc.apply_answer(req, "allow", source="telegram")
    req.refresh_from_db()
    assert req.resolved_by == PermissionRequest.ResolvedBy.TELEGRAM


def test_claim_encodes_source():
    import fakeredis

    from panel.core import bus

    client = fakeredis.FakeStrictRedis(server=fakeredis.FakeServer())
    perm_svc.claim_answer_sync(client, "rid", "allow", source="telegram")
    assert client.get(bus.key_answer("rid")) == b"allow|telegram"
    assert perm_svc._split_answer("allow|telegram") == ("allow", "telegram")
    assert perm_svc._split_answer("allow") == ("allow", "web")  # legacy


def test_apply_answer_idempotent(monkeypatch):
    from panel.core.services import privileged

    monkeypatch.setattr(privileged, "run_render", lambda: None)
    session = _session()
    req = perm_svc.create_request(session, "Bash", {}, 900)
    perm_svc.apply_answer(req, "allow")
    perm_svc.apply_answer(req, "deny")  # segunda no cambia nada
    req.refresh_from_db()
    assert req.status == PermissionRequest.Status.ALLOWED


# ---------- expire_pending ----------

def test_expire_pending_marks_expired():
    session = _session()
    perm_svc.create_request(session, "Bash", {}, 900)
    perm_svc.create_request(session, "Read", {}, 900)
    assert perm_svc.expire_pending(session) == 2
    assert not PermissionRequest.objects.filter(
        session=session, status=PermissionRequest.Status.PENDING
    ).exists()


# ---------- rewrite ----------

def test_rewrite_dummy_renames(monkeypatch):
    monkeypatch.setenv("PANEL_REWRITE_DUMMY", "1")
    hooks = rewrite.get_hooks()
    out, changed = rewrite.apply_rewrites(
        "Write", {"file_path": "/srv/projects/demo/ORIGINAL.txt", "content": "x"}, hooks
    )
    assert changed is True
    assert out["file_path"].endswith("REWRITTEN.txt")


def test_rewrite_no_hooks_no_change():
    out, changed = rewrite.apply_rewrites("Write", {"file_path": "/a/b.txt"}, [])
    assert changed is False and out == {"file_path": "/a/b.txt"}


def test_create_request_stores_rewritten_preview(monkeypatch):
    monkeypatch.setenv("PANEL_REWRITE_DUMMY", "1")
    session = _session()
    hooks = rewrite.get_hooks()
    effective, _ = rewrite.apply_rewrites(
        "Write", {"file_path": "/srv/projects/demo/ORIGINAL.txt"}, hooks
    )
    req = perm_svc.create_request(session, "Write", effective, 900)
    assert "REWRITTEN.txt" in req.input_preview


# ---------- carrera de respuestas (threads reales) ----------

def test_concurrent_answers_one_wins():
    server = fakeredis.FakeServer()
    request_id = "abc-123"
    results = []
    barrier = threading.Barrier(2)

    def worker(answer):
        client = fakeredis.FakeStrictRedis(server=server)
        barrier.wait()
        results.append(perm_svc.claim_answer_sync(client, request_id, answer))

    threads = [
        threading.Thread(target=worker, args=("allow",)),
        threading.Thread(target=worker, args=("deny",)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [False, True]  # exactamente uno reclama
    val = fakeredis.FakeStrictRedis(server=server).get(bus.key_answer(request_id))
    assert val in (b"allow|web", b"deny|web")


def test_claim_rejects_invalid_answer():
    client = fakeredis.FakeStrictRedis(server=fakeredis.FakeServer())
    with pytest.raises(ValueError):
        perm_svc.claim_answer_sync(client, "x", "maybe")
