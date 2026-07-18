"""WebSocket de sesión: auth (§4.4), backlog desde PG y no-duplicación."""

import json

import fakeredis.aioredis
import pytest
from channels.testing.websocket import WebsocketCommunicator

from panel.core import consumers
from panel.core.models import ModelProfile, PermissionPolicy, Project, Session
from panel.core.services import events as event_svc

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
    connected, code = await comm.connect()
    assert connected is False
    assert code == 4401


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
    seqs = []
    for _ in range(2):
        msg = json.loads(await comm.receive_from())
        assert msg["channel"] == "out"
        seqs.append(msg["event"]["seq"])
    assert seqs == [2, 3]
    await comm.disconnect()
