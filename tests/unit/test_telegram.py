"""Telegram (Gate 4): webhook (secret inválido→403, allowlist, doble tap),
truncados (preview 500, mensaje ≤4096), callback_data ≤64, y recreación de topic
borrado. Las llamadas al Bot API se mockean."""

from __future__ import annotations

import json

import fakeredis
import pytest
from django.utils import timezone

from panel.core.models import (
    Config,
    ModelProfile,
    PermissionPolicy,
    PermissionRequest,
    Project,
    Session,
)
from panel.core.services import telegram as tg
from panel.core.services import tg_notify

pytestmark = pytest.mark.django_db


def _req(tool="Bash", preview="x", tg_message_id=None, topic=7):
    profile = ModelProfile.objects.create(name="m", provider="anthropic", model="x")
    policy = PermissionPolicy.objects.create(name="p")
    project = Project.objects.create(
        slug="demo", name="Demo", path="/srv/projects/demo",
        model_profile=profile, permission_policy=policy, telegram_topic_id=topic,
    )
    session = Session.objects.create(project=project)
    return PermissionRequest.objects.create(
        session=session, tool=tool, input_full={}, input_preview=preview,
        status=PermissionRequest.Status.PENDING, tg_message_id=tg_message_id,
        expires_at=timezone.now() + timezone.timedelta(minutes=15),
    )


# ---------- formato / límites ----------

def test_callback_data_within_64_bytes():
    kb = tg.keyboard_for("123e4567-e89b-12d3-a456-426614174000")
    for row in kb["inline_keyboard"]:
        for btn in row:
            assert len(btn["callback_data"].encode()) <= tg.CALLBACK_DATA_LIMIT


def test_parse_callback_data():
    assert tg.parse_callback_data("allow:abc") == ("allow", "abc")
    assert tg.parse_callback_data("allow_always:xy") == ("allow_always", "xy")
    assert tg.parse_callback_data("bogus:abc") is None
    assert tg.parse_callback_data("allow") is None


def test_format_request_truncates_to_4096():
    req = _req(preview="P" * 500)
    # aunque el preview ya está acotado, forzamos un tool gigante
    req.tool = "T" * 6000
    text = tg.format_request(req)
    assert len(text) <= tg.TG_TEXT_LIMIT


def test_send_message_truncates(monkeypatch):
    captured = {}
    monkeypatch.setattr(tg.settings, "TELEGRAM_BOT_TOKEN", "x")

    class Resp:
        def json(self):
            captured["called"] = True
            return {"ok": True, "result": {"message_id": 1}}

    monkeypatch.setattr(tg.httpx, "post", lambda *a, **k: (captured.update(k), Resp())[1])
    tg.send_message(123, "Z" * 9000)
    assert len(captured["json"]["text"]) == tg.TG_TEXT_LIMIT


# ---------- webhook ----------

def _post(client, body: dict, secret_header: str | None):
    headers = {}
    if secret_header is not None:
        headers["HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN"] = secret_header
    return client.post(
        "/tg/webhook", data=json.dumps(body), content_type="application/json", **headers
    )


def test_webhook_bad_secret_403(client, settings):
    Config.set("tg_webhook_secret", "right")
    resp = _post(client, {"callback_query": {}}, "wrong")
    assert resp.status_code == 403


def test_webhook_missing_secret_403(client):
    Config.set("tg_webhook_secret", "right")
    resp = _post(client, {"callback_query": {}}, None)
    assert resp.status_code == 403


def test_webhook_user_not_in_allowlist_ignored(client, settings, monkeypatch):
    Config.set("tg_webhook_secret", "s")
    settings.TELEGRAM_USER_IDS = [999]
    called = []
    monkeypatch.setattr(tg, "answer_callback_query", lambda *a, **k: called.append(a))
    body = {"callback_query": {"id": "c", "from": {"id": 111}, "data": "allow:x"}}
    resp = _post(client, body, "s")
    assert resp.status_code == 200
    assert called == []  # ignorado en silencio


def test_webhook_message_update_ignored(client):
    Config.set("tg_webhook_secret", "s")
    resp = _post(client, {"message": {"text": "hola"}}, "s")
    assert resp.status_code == 200


def test_webhook_double_tap_says_already(client, settings, monkeypatch):
    Config.set("tg_webhook_secret", "s")
    settings.TELEGRAM_USER_IDS = [111]
    req = _req()
    server = fakeredis.FakeServer()
    from panel.ui import views

    monkeypatch.setattr(
        views.redis, "from_url", lambda _url: fakeredis.FakeStrictRedis(server=server)
    )
    answers = []
    monkeypatch.setattr(
        tg, "answer_callback_query", lambda cid, text="": answers.append(text)
    )

    body = {"callback_query": {"id": "c1", "from": {"id": 111}, "data": f"allow:{req.id}"}}
    _post(client, body, "s")  # primer tap: reclama
    body2 = {"callback_query": {"id": "c2", "from": {"id": 111}, "data": f"deny:{req.id}"}}
    _post(client, body2, "s")  # segundo tap: ya respondida
    assert any("Registrado" in a for a in answers)
    assert any("Ya fue respondida" in a for a in answers)


# ---------- notify (bridge) ----------

def test_notify_resolved_edits_and_removes_keyboard(monkeypatch):
    Config.set("tg_chat_id", "-100123")
    req = _req(tg_message_id=555)
    calls = {}
    monkeypatch.setattr(tg, "edit_message_text", lambda chat, mid, text, **k: calls.update(
        chat=chat, mid=mid, text=text, reply_markup=k.get("reply_markup")))
    tg_notify.notify_resolved(str(req.id), "timeout")
    assert calls["mid"] == 555
    assert calls["reply_markup"] is None  # teclado quitado
    assert "Expiró" in calls["text"]


def test_notify_request_recreates_deleted_topic(monkeypatch):
    Config.set("tg_chat_id", "-100123")
    req = _req(topic=42)
    attempts = {"send": 0}

    def fake_send(chat_id, text, *, thread_id=None, reply_markup=None):
        attempts["send"] += 1
        if attempts["send"] == 1:
            raise tg.TelegramError("sendMessage", 400, "message thread not found")
        return 888

    monkeypatch.setattr(tg, "send_message", fake_send)
    monkeypatch.setattr(tg, "create_forum_topic", lambda chat_id, name: 99)
    tg_notify.notify_request(str(req.id))
    req.refresh_from_db()
    assert req.session.project.telegram_topic_id == 99  # topic recreado
    assert req.tg_message_id == 888
