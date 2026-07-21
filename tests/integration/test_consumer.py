"""WebSocket de sesión: auth (§4.4), backlog desde PG y no-duplicación."""

import json
import uuid
from datetime import timedelta

import fakeredis.aioredis
import pytest
from channels.testing.websocket import WebsocketCommunicator
from django.utils import timezone as djtz

from panel.core import bus, consumers
from panel.core.models import (
    ModelProfile,
    PermissionPolicy,
    PermissionRequest,
    Project,
    Session,
)
from panel.core.services import events as event_svc
from panel.core.services import permissions as perm_svc

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.asyncio]


def _make_session():
    profile = ModelProfile.objects.create(
        name="p", provider=ModelProfile.Provider.ANTHROPIC, model="m"
    )
    policy = PermissionPolicy.objects.create(name="auto", mode=PermissionPolicy.Mode.AUTO)
    project = Project.objects.create(
        slug="demo",
        name="Demo",
        path="/srv/projects/demo",
        model_profile=profile,
        permission_policy=policy,
    )
    return Session.objects.create(project=project)


def _patch_fake_redis(monkeypatch):
    server = fakeredis.FakeServer()
    monkeypatch.setattr(
        consumers, "make_redis", lambda: fakeredis.aioredis.FakeRedis(server=server)
    )
    return server


async def _connect(sid, last_seq=0):
    from panel.core.consumers import SessionConsumer

    comm = WebsocketCommunicator(
        SessionConsumer.as_asgi(), f"/ws/session/{sid}?last_seq={last_seq}"
    )
    comm.scope["url_route"] = {"kwargs": {"sid": sid}}
    return comm


async def test_rejects_unauthenticated():
    from django.contrib.auth.models import AnonymousUser

    from panel.core.consumers import SessionConsumer

    comm = WebsocketCommunicator(SessionConsumer.as_asgi(), "/ws/session/x")
    comm.scope["url_route"] = {"kwargs": {"sid": "x"}}
    comm.scope["user"] = AnonymousUser()
    # Se acepta y luego se cierra con 4401 (para que el código sea observable
    # por el cliente, no un rechazo HTTP 403 con code 1006).
    connected, _ = await comm.connect()
    assert connected is True
    out = await comm.receive_output()
    assert out["type"] == "websocket.close"
    assert out["code"] == 4401
    await comm.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_sends_backlog_and_dedups(monkeypatch, django_user_model):
    from channels.db import database_sync_to_async

    _patch_fake_redis(monkeypatch)
    session = await database_sync_to_async(_make_session)()
    sid = str(session.id)

    @database_sync_to_async
    def seed():
        for i in range(1, 4):
            event_svc.persist_event(session, i, "assistant", {"n": i})

    await seed()
    user = await database_sync_to_async(django_user_model.objects.create_user)(
        username="u", password="x"
    )

    comm = await _connect(sid, last_seq=1)
    comm.scope["user"] = user
    connected, _ = await comm.connect()
    assert connected

    # Backlog: seq 2 y 3 (seq>1), en orden, sin duplicar.
    # FASE C: el consumer ahora publica el shape plano (spread del dict
    # del evento + _channel/_event para compat). El cliente extrae seq del
    # top-level; "_channel" sigue siendo "out" para identificar el canal.
    seqs = []
    for _ in range(2):
        msg = json.loads(await comm.receive_from())
        assert msg["_channel"] == "out"
        assert msg["seq"] == msg["_event"]["seq"]
        seqs.append(msg["seq"])
    assert seqs == [2, 3]
    await comm.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_perm_message_is_wrapped_as_permission_request_uievent(
    monkeypatch, django_user_model
):
    """SP7: el mensaje del canal `perm` debe llegar al WS con shape
    RawEventMessage {seq, type, payload, ui_event} para que el chat lo
    renderice como bubble con botones. Antes llegaba como dict plano
    (serialize_request) y el cliente lo descartaba en silencio
    (ingestEvent: `if (!msg.ui_event) return`)."""
    from channels.db import database_sync_to_async

    server = _patch_fake_redis(monkeypatch)
    session = await database_sync_to_async(_make_session)()
    sid = str(session.id)

    @database_sync_to_async
    def seed_req():
        return PermissionRequest.objects.create(
            session=session,
            tool="Bash",
            input_full={"command": "rm -rf /"},
            input_preview="rm -rf /",
            expires_at=djtz.now() + timedelta(minutes=15),
        )

    req = await seed_req()
    perm_id = str(req.id)

    user = await database_sync_to_async(django_user_model.objects.create_user)(
        username="perm-u", password="x"
    )

    comm = await _connect(sid, last_seq=0)
    comm.scope["user"] = user
    connected, _ = await comm.connect()
    assert connected
    # Drena el backlog vacío.
    # Dar tiempo al reader task del consumer a suscribirse antes de publicar
    # (subscribe + create_task son await'd en connect() pero el bucle listen
    # puede no haber arrancado todavía cuando volvemos aquí).
    import asyncio as _aio
    await _aio.sleep(0.1)
    # Publicar manualmente como lo hace el worker en publish_request().
    fake_redis = consumers.make_redis()
    await fake_redis.publish(
        bus.key_perm(sid),
        json.dumps(perm_svc.serialize_request(req)),
    )
    msg = json.loads(await comm.receive_from(timeout=3))
    await comm.disconnect()

    assert msg["_channel"] == "perm"
    # Identifica al perm request — el cliente lo usa para dedupe y para
    # asociarlo al bubble.
    assert msg["id"] == perm_id
    # Seq sintético estable derivado del uuid (no un literal: el cliente lo
    # añade a seenSeq y un -1 fijo invitaría a duplicar perm requests).
    assert isinstance(msg["seq"], int)
    assert msg["seq"] == consumers._perm_seq(perm_id)
    # Shape de UIEvent v1 permission_request — esto es lo que el chat
    # necesita para que ingestEvent NO lo descarte.
    assert msg["type"] == "permission_request"
    assert msg["ui_event"]["kind"] == "permission_request"
    assert msg["ui_event"]["payload"]["id"] == perm_id
    assert msg["ui_event"]["payload"]["tool"] == "Bash"
    assert msg["ui_event"]["payload"]["input_preview"] == "rm -rf /"


@pytest.mark.django_db(transaction=True)
async def test_perm_message_with_missing_id_is_dropped(
    monkeypatch, django_user_model
):
    """SP7: defensa — si el payload no trae `id` (no debería pasar, pero el
    canal `perm` es público entre panel/worker), no propagamos un bubble
    roto. Sin id no podemos ni dedupear ni resolver."""
    from channels.db import database_sync_to_async

    _patch_fake_redis(monkeypatch)
    session = await database_sync_to_async(_make_session)()
    sid = str(session.id)
    user = await database_sync_to_async(django_user_model.objects.create_user)(
        username="perm-u2", password="x"
    )
    comm = await _connect(sid, last_seq=0)
    comm.scope["user"] = user
    connected, _ = await comm.connect()
    assert connected
    import asyncio as _aio
    await _aio.sleep(0.1)

    fake_redis = consumers.make_redis()
    await fake_redis.publish(
        bus.key_perm(sid),
        json.dumps({"tool": "Bash", "input_preview": "x"}),  # sin id
    )
    # Nada en 0.5s — el consumer lo descarta.
    with pytest.raises(_aio.TimeoutError):
        await comm.receive_from(timeout=0.5)
