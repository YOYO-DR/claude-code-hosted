"""API v1: auth + sesiones + projects (con path traversal)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from panel.core.models import (
    McpServer,
    ModelProfile,
    PermissionPolicy,
    Project,
    Session,
)
from panel.core.services import permissions as perm_svc

pytestmark = pytest.mark.django_db(transaction=True)


# ---------- helpers ----------

def _make_verified_user(username="api-tester"):
    User = get_user_model()
    user = User.objects.create_user(username=username, password="x")
    from django_otp.plugins.otp_totp.models import TOTPDevice
    TOTPDevice.objects.create(user=user, name="default", confirmed=True)
    return user


@pytest.fixture(autouse=True)
def _force_otp_verified():
    """Por defecto los tests asumen `user.is_verified` truthy. Tests
    individuales pueden sobreescribir importando `_force_otp_verified`
    directamente."""
    import panel.api_v1.auth as auth_mod
    original = auth_mod._is_verified
    auth_mod._is_verified = lambda request: True
    yield
    auth_mod._is_verified = original


def _profile(name="p"):
    return ModelProfile.objects.create(name=name, provider="anthropic", model="m")


def _policy(name="pol"):
    return PermissionPolicy.objects.create(name=name)


def _project(slug, tmp_path, **extra):
    p = Project.objects.create(
        slug=slug, name=slug.title(),
        path=str(tmp_path / "srv" / slug),
        model_profile=_profile(f"prof-{slug}"),
        permission_policy=_policy(f"pol-{slug}"),
        **extra,
    )
    return p


def _client_verified(user=None):
    client = Client()
    if user is None:
        user = _make_verified_user()
    client.force_login(user)
    return client


# ---------- /api/v1/me/ ----------

def test_me_requires_authentication():
    c = Client()
    r = c.get("/api/v1/me/")
    assert r.status_code == 401
    assert r.json()["detail"] == "unauthenticated"


def test_me_returns_authenticated_user():
    user = _make_verified_user()
    c = _client_verified(user)
    r = c.get("/api/v1/me/")
    assert r.status_code == 200
    body = r.json()
    assert body["username"] == user.username
    assert body["is_verified"] is True


def test_me_sets_csrf_cookie():
    """El SPA necesita la cookie csrftoken para poder hacer POST."""
    user = _make_verified_user()
    c = _client_verified(user)
    r = c.get("/api/v1/me/")
    assert "csrftoken" in r.cookies


# ---------- /api/v1/login/ ----------

def test_login_with_bad_credentials_returns_401():
    r = Client().post(
        "/api/v1/login/",
        data=json.dumps({"username": "nope", "password": "wrong"}),
        content_type="application/json",
    )
    assert r.status_code == 401
    assert r.json()["ok"] is False


def test_login_success_without_otp_returns_ok_with_user(monkeypatch):
    """Login sin OTP → ok pero is_verified=False. Restauramos la lógica
    real de _is_verified (que el fixture parchea a True)."""
    from django.contrib.auth import get_user_model

    import panel.api_v1.auth as auth_mod

    # Restaurar la lógica real: callable que retorna False si no hay device.
    User = get_user_model()
    def real_is_verified(request):
        val = getattr(request.user, "is_verified", None)
        if val is None:
            return False
        if callable(val):
            try:
                return bool(val())
            except TypeError:
                return False
        return bool(val)
    monkeypatch.setattr(auth_mod, "_is_verified", real_is_verified)

    User.objects.create_user(username="login-tester", password="test-pass-123")
    c = Client()
    r = c.post(
        "/api/v1/login/",
        data=json.dumps({"username": "login-tester", "password": "test-pass-123"}),
        content_type="application/json",
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["user"]["username"] == "login-tester"
    assert body["user"]["is_verified"] is False  # sin TOTP


# ---------- /api/v1/sessions/ ----------

def test_sessions_list_returns_sessions(tmp_path):
    p = _project("alpha", tmp_path)
    Session.objects.create(project=p, status=Session.Status.IDLE)
    c = _client_verified()
    r = c.get("/api/v1/sessions/")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["project"] == "alpha"


def test_session_message_sends_to_redis(monkeypatch, tmp_path):
    """POST message → LPUSH en Redis (lo que la vista hace)."""
    import redis as sync_redis
    p = _project("msg-test", tmp_path)
    s = Session.objects.create(project=p, status=Session.Status.IDLE)
    fake = __import__("fakeredis").FakeStrictRedis()
    monkeypatch.setattr(sync_redis, "from_url", lambda url: fake)
    c = _client_verified()
    r = c.post(
        f"/api/v1/sessions/{s.id}/message/",
        data=json.dumps({"text": "hola agente"}),
        content_type="application/json",
    )
    assert r.status_code == 200
    from panel.core import bus
    items = fake.lrange(bus.key_in(str(s.id)), 0, -1)
    assert len(items) == 1
    payload = json.loads(items[0])
    assert payload["type"] == "user_message"
    assert payload["text"] == "hola agente"


def test_session_message_rejects_wrong_state(tmp_path):
    p = _project("stopped", tmp_path)
    s = Session.objects.create(project=p, status=Session.Status.STOPPED)
    c = _client_verified()
    r = c.post(
        f"/api/v1/sessions/{s.id}/message/",
        data=json.dumps({"text": "x"}),
        content_type="application/json",
    )
    assert r.status_code == 409


# ---------- /api/v1/projects/ ----------

def test_projects_list(tmp_path):
    _project("alpha", tmp_path)
    _project("beta", tmp_path)
    c = _client_verified()
    r = c.get("/api/v1/projects/")
    assert r.status_code == 200
    data = r.json()
    slugs = {p["slug"] for p in data}
    assert slugs == {"alpha", "beta"}


def test_project_tree_returns_entries(tmp_path):
    p = _project("tree", tmp_path)
    base = Path(p.path)
    base.mkdir(parents=True, exist_ok=True)
    (base / "a.py").write_text("x")
    (base / "sub").mkdir()
    c = _client_verified()
    r = c.get(f"/api/v1/projects/{p.slug}/tree/")
    assert r.status_code == 200
    data = r.json()
    names = {e["name"] for e in data["entries"]}
    assert {"a.py", "sub"} <= names


# ---------- Path traversal: la parte crítica de C.5 ----------

def test_project_file_blocks_parent_traversal(tmp_path):
    p = _project("traverse", tmp_path)
    Path(p.path).mkdir(parents=True, exist_ok=True)
    secret = Path("/tmp/api-v1-secret.txt")
    secret.write_text("SECRETO")
    c = _client_verified()
    r = c.get(f"/api/v1/projects/{p.slug}/file/?path=../../api-v1-secret.txt")
    assert r.status_code == 403
    assert "fuera" in r.json()["error"]


def test_project_file_blocks_absolute_path(tmp_path):
    p = _project("traverse2", tmp_path)
    Path(p.path).mkdir(parents=True, exist_ok=True)
    c = _client_verified()
    r = c.get(f"/api/v1/projects/{p.slug}/file/?path=/etc/passwd")
    assert r.status_code == 403


def test_project_file_blocks_dotdot_inside(tmp_path):
    """`subdir/../../etc` también debe ser 403."""
    p = _project("traverse3", tmp_path)
    Path(p.path).mkdir(parents=True, exist_ok=True)
    c = _client_verified()
    r = c.get(f"/api/v1/projects/{p.slug}/file/?path=subdir/../../etc/passwd")
    assert r.status_code == 403


def test_project_file_symlink_escape_blocked(tmp_path):
    """Symlink dentro del proyecto apuntando fuera → 403."""
    p = _project("symlink", tmp_path)
    base = Path(p.path)
    base.mkdir(parents=True, exist_ok=True)
    secret = Path("/tmp/api-v1-secret2.txt")
    secret.write_text("SECRETO2")
    (base / "escape").symlink_to(secret)
    c = _client_verified()
    r = c.get(f"/api/v1/projects/{p.slug}/file/?path=escape")
    assert r.status_code == 403


def test_project_file_inside_project_works(tmp_path):
    p = _project("inside", tmp_path)
    base = Path(p.path)
    base.mkdir(parents=True, exist_ok=True)
    (base / "ok.py").write_text("print('hola')")
    c = _client_verified()
    r = c.get(f"/api/v1/projects/{p.slug}/file/?path=ok.py")
    assert r.status_code == 200
    body = r.json()
    assert body["is_binary"] is False
    assert "hola" in body["content"]


def test_project_file_truncates_large_files(tmp_path):
    p = _project("big", tmp_path)
    base = Path(p.path)
    base.mkdir(parents=True, exist_ok=True)
    big = "x" * (200 * 1024)  # 200 KB > cap 100 KB
    (base / "huge.txt").write_text(big)
    c = _client_verified()
    r = c.get(f"/api/v1/projects/{p.slug}/file/?path=huge.txt")
    assert r.status_code == 200
    body = r.json()
    assert body["truncated"] is True
    assert len(body["content"]) == 100 * 1024


def test_project_file_binary_returns_metadata_only(tmp_path):
    p = _project("binary", tmp_path)
    base = Path(p.path)
    base.mkdir(parents=True, exist_ok=True)
    (base / "img.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")
    c = _client_verified()
    r = c.get(f"/api/v1/projects/{p.slug}/file/?path=img.png")
    assert r.status_code == 200
    body = r.json()
    assert body["is_binary"] is True
    assert body["content"] is None


def test_project_diff_runs_git(tmp_path):
    p = _project("diff", tmp_path)
    base = Path(p.path)
    base.mkdir(parents=True, exist_ok=True)
    # Inicializar git para que git diff funcione.
    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=base, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=base, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=base, check=True)
    (base / "a.py").write_text("uno")
    subprocess.run(["git", "add", "."], cwd=base, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=base, check=True)
    (base / "a.py").write_text("dos")
    c = _client_verified()
    r = c.get(f"/api/v1/projects/{p.slug}/diff/")
    assert r.status_code == 200
    body = r.json()
    assert "-uno" in body["diff"]
    assert "+dos" in body["diff"]
    assert body["dirty"] is True


# ---------- /api/v1/permissions/ (regression D11) ----------

def test_permissions_list_filters_by_live_pending(tmp_path):
    """La cola NO debe traer pending de sesión muerta (D11)."""
    p = _project("live", tmp_path)
    p_path = Path(p.path)
    p_path.mkdir(parents=True, exist_ok=True)
    s_live = Session.objects.create(project=p, status=Session.Status.IDLE)
    s_dead = Session.objects.create(project=p, status=Session.Status.STOPPED)
    perm_svc.create_request(s_live, "Bash", {"command": "ls"}, 900)
    perm_svc.create_request(s_dead, "Bash", {"command": "ls"}, 900)
    c = _client_verified()
    r = c.get("/api/v1/permissions/")
    assert r.status_code == 200
    data = r.json()
    # Solo la de sesión live
    assert len(data) == 1


def test_permissions_resolve_returns_409_on_double_resolve(monkeypatch, tmp_path):
    import redis as sync_redis
    p = _project("double", tmp_path)
    Path(p.path).mkdir(parents=True, exist_ok=True)
    s = Session.objects.create(project=p, status=Session.Status.IDLE)
    req = perm_svc.create_request(s, "Bash", {"command": "ls"}, 900)

    fake_redis = __import__("fakeredis").FakeStrictRedis()
    monkeypatch.setattr(sync_redis, "from_url", lambda url: fake_redis)

    c = _client_verified()
    r1 = c.post(
        f"/api/v1/permissions/{req.id}/resolve/",
        data=json.dumps({"answer": "allow"}),
        content_type="application/json",
    )
    assert r1.status_code == 200
    assert r1.json()["ok"] is True
    r2 = c.post(
        f"/api/v1/permissions/{req.id}/resolve/",
        data=json.dumps({"answer": "deny"}),
        content_type="application/json",
    )
    assert r2.status_code == 409  # claimed=False → conflict
    assert r2.json()["conflict"] is True


# ---------- /api/v1/mcps/ ----------

def test_mcps_list_returns_servers(tmp_path):
    _project("mcps", tmp_path)
    McpServer.objects.create(
        name="ports", scope=McpServer.Scope.GLOBAL,
        transport=McpServer.Transport.HTTP, config={"url": "http://x"},
    )
    c = _client_verified()
    r = c.get("/api/v1/mcps/")
    assert r.status_code == 200
    data = r.json()
    assert any(m["name"] == "ports" for m in data)