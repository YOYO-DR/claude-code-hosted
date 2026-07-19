"""Monitor de salud (§6.2): cooldown de alertas, marcado de sesiones sin
heartbeat como crashed, umbral de disco."""

from __future__ import annotations

import fakeredis
import pytest
from django.utils import timezone

from panel.core import bus
from panel.core.models import (
    ModelProfile,
    PermissionPolicy,
    Project,
    Session,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def mon():
    from scripts import monitor

    return monitor


def _session(status, updated_ago_s=60):
    profile = ModelProfile.objects.create(name="m", provider="anthropic", model="x")
    policy = PermissionPolicy.objects.create(name="p")
    project = Project.objects.create(
        slug="demo", name="Demo", path="/srv/projects/demo",
        model_profile=profile, permission_policy=policy,
    )
    s = Session.objects.create(project=project, status=status)
    # forzar updated_at al pasado (bypassa auto_now)
    Session.objects.filter(pk=s.pk).update(
        updated_at=timezone.now() - timezone.timedelta(seconds=updated_ago_s)
    )
    s.refresh_from_db()
    return s


def test_cooldown_dedupes(mon):
    assert mon._should_alert("x") is True
    assert mon._should_alert("x") is False  # dentro del cooldown


def test_heartbeat_missing_marks_crashed(mon):
    s = _session(Session.Status.RUNNING, updated_ago_s=60)
    r = fakeredis.FakeStrictRedis(server=fakeredis.FakeServer())  # sin heartbeat
    mon.check_heartbeats(r)
    s.refresh_from_db()
    assert s.status == Session.Status.CRASHED


def test_heartbeat_present_keeps_running(mon):
    s = _session(Session.Status.RUNNING, updated_ago_s=60)
    r = fakeredis.FakeStrictRedis(server=fakeredis.FakeServer())
    r.set(bus.key_heartbeat(str(s.id)), "alive")
    mon.check_heartbeats(r)
    s.refresh_from_db()
    assert s.status == Session.Status.RUNNING


def test_fresh_session_within_grace_not_flagged(mon):
    # recién actualizada (dentro del grace) → no se toca aunque no haya heartbeat
    s = _session(Session.Status.STARTING, updated_ago_s=1)
    r = fakeredis.FakeStrictRedis(server=fakeredis.FakeServer())
    mon.check_heartbeats(r)
    s.refresh_from_db()
    assert s.status == Session.Status.STARTING


def test_disk_alert_threshold(mon, monkeypatch):
    alerts = []
    monkeypatch.setattr(mon, "_alert", lambda t: alerts.append(t))

    class FakeStat:
        f_blocks = 100
        f_bavail = 5  # 95% usado

    monkeypatch.setattr(mon.os, "statvfs", lambda _p: FakeStat())
    mon.check_disk()
    assert alerts and "95%" in alerts[0]


def test_disk_ok_no_alert(mon, monkeypatch):
    alerts = []
    monkeypatch.setattr(mon, "_alert", lambda t: alerts.append(t))

    class FakeStat:
        f_blocks = 100
        f_bavail = 50  # 50% usado

    monkeypatch.setattr(mon.os, "statvfs", lambda _p: FakeStat())
    mon.check_disk()
    assert alerts == []
