"""Persistencia de eventos: seq inicial y idempotencia ante (session, seq)."""

import pytest

from panel.core.models import (
    ModelProfile,
    PermissionPolicy,
    Project,
    Session,
)
from panel.core.services import events as event_svc

pytestmark = pytest.mark.django_db


def _make_session() -> Session:
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


def test_initial_seq_empty_is_one():
    s = _make_session()
    assert event_svc.initial_seq(s) == 1


def test_initial_seq_after_events():
    s = _make_session()
    event_svc.persist_event(s, 1, "assistant", {})
    event_svc.persist_event(s, 2, "result", {})
    assert event_svc.initial_seq(s) == 3


def test_persist_is_idempotent_on_duplicate_seq():
    s = _make_session()
    first = event_svc.persist_event(s, 1, "assistant", {"x": 1})
    dup = event_svc.persist_event(s, 1, "assistant", {"x": 1})
    assert first is not None
    assert dup is None  # (session, seq) único → no duplica
    assert s.events.count() == 1
