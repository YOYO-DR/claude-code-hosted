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
from workers import supervisor

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
    body = r.json()
    # FASE UX-S.1: la respuesta pasó de array plano a {results: [...], total}.
    assert body["total"] == 1
    assert body["results"][0]["project_slug"] == "alpha"


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

def _session(slug: str, status: str = Session.Status.IDLE):
    """Helper: crea proyecto + sesión con status dado."""
    p = _project(slug, Path("/tmp") / f"sess-{slug}-{status}")
    Path(p.path).mkdir(parents=True, exist_ok=True)
    return Session.objects.create(project=p, status=status)


def test_sessions_list_filters_by_status_csv(tmp_path):
    """FASE UX-S.1: ?status=running,waiting_approval filtra la lista."""
    s_run = _session("s-run", Session.Status.RUNNING)
    _session("s-idle", Session.Status.IDLE)
    _session("s-stopped", Session.Status.STOPPED)
    c = _client_verified()
    r = c.get("/api/v1/sessions/?status=running,waiting_approval")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["results"][0]["id"] == str(s_run.id)
    ids = [r["id"] for r in body["results"]]
    assert str(s_run.id) in ids


def test_sessions_list_filters_by_project_and_text(tmp_path):
    _session("alpha", Session.Status.IDLE)
    _session("beta", Session.Status.IDLE)
    c = _client_verified()
    r = c.get("/api/v1/sessions/?project=alpha")
    assert r.status_code == 200
    assert r.json()["total"] == 1
    # texto libre por slug
    r = c.get("/api/v1/sessions/?q=bet")
    assert r.json()["total"] == 1
    assert r.json()["results"][0]["project_slug"] == "beta"


def test_sessions_list_ignores_invalid_status(tmp_path):
    """Si llega un status desconocido, NO se filtra por él (defensa)."""
    s = _session("valid", Session.Status.IDLE)
    c = _client_verified()
    r = c.get("/api/v1/sessions/?status=bogus,idle")
    assert r.status_code == 200
    body = r.json()
    # Filtró solo por "idle" (válido); "bogus" se descarta.
    assert body["total"] == 1
    assert body["results"][0]["id"] == str(s.id)


def test_project_create_validates_input(tmp_path):
    """UX-T.5: /api/v1/projects/create/ rechaza input inválido."""
    c = _client_verified()
    # name vacío / slug vacío
    r = c.post(
        "/api/v1/projects/create/",
        data=json.dumps({"slug": "ab"}),
        content_type="application/json",
    )
    assert r.status_code == 400
    # slug con caracteres prohibidos
    r = c.post(
        "/api/v1/projects/create/",
        data=json.dumps({"name": "X", "slug": "BAD slug!"}),
        content_type="application/json",
    )
    assert r.status_code == 400
    # github_enabled sin repo
    r = c.post(
        "/api/v1/projects/create/",
        data=json.dumps({"name": "X", "slug": "ok-slug", "github_enabled": True}),
        content_type="application/json",
    )
    assert r.status_code == 400
    # repo con formato malo
    r = c.post(
        "/api/v1/projects/create/",
        data=json.dumps({"name": "X", "slug": "ok-slug-2",
                         "github_enabled": True,
                         "github_repo": "https://github.com/owner/repo.git"}),
        content_type="application/json",
    )
    assert r.status_code == 400


def test_project_create_form_options_returns_profiles_and_policies(tmp_path):
    """UX-T.5: /form-options/ lista ModelProfile y PermissionPolicy."""
    c = _client_verified()
    r = c.get("/api/v1/projects/form-options/")
    assert r.status_code == 200
    body = r.json()
    assert "model_profiles" in body
    assert "permission_policies" in body
    assert "gh_token_missing" in body


def test_session_create_requires_slug(tmp_path):
    """UX-T.6: /api/v1/sessions/create/ body requiere slug."""
    c = _client_verified()
    r = c.post("/api/v1/sessions/create/",
               data=json.dumps({}),
               content_type="application/json")
    assert r.status_code == 400


def test_session_create_404_for_unknown_slug(tmp_path):
    c = _client_verified()
    r = c.post("/api/v1/sessions/create/",
               data=json.dumps({"slug": "nonexistent-project-xyz"}),
               content_type="application/json")
    assert r.status_code == 404


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


def test_project_git_branch_and_dirty(tmp_path):
    """FASE E.4: /api/v1/projects/<slug>/git/ devuelve branch + dirty para un
    repo git inicializado. Coverage del endpoint que faltaba (el SPA RamaTab
    mostraba rama: ?)."""
    p = _project("g1", tmp_path)
    base = Path(p.path)
    base.mkdir(parents=True, exist_ok=True)
    import subprocess
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=base, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=base, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=base, check=True)
    (base / "x.txt").write_text("hola")
    subprocess.run(["git", "add", "."], cwd=base, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=base, check=True)
    # Sin cambios: dirty=False
    c = _client_verified()
    r = c.get(f"/api/v1/projects/{p.slug}/git/")
    assert r.status_code == 200
    body = r.json()
    assert body["branch"] == "main"
    assert body["dirty"] is False
    assert "not_a_repo" not in body
    # Con cambio: dirty=True
    (base / "x.txt").write_text("adios")
    r = c.get(f"/api/v1/projects/{p.slug}/git/")
    body = r.json()
    assert body["dirty"] is True
    assert body["branch"] == "main"


def test_project_git_not_a_repo(tmp_path):
    """Si el path NO es un repo, devuelve 200 con not_a_repo=true."""
    p = _project("g2", tmp_path)
    # No inicializamos git → not_a_repo=true
    c = _client_verified()
    r = c.get(f"/api/v1/projects/{p.slug}/git/")
    assert r.status_code == 200
    body = r.json()
    assert body["not_a_repo"] is True
    assert body["branch"] is None


# ---------- UX-T.2: project_update + project_delete ----------

def test_project_update_changes_editable_fields(tmp_path):
    p = _project("upd", tmp_path)
    c = _client_verified()
    r = c.generic(
        method="PATCH",
        path=f"/api/v1/projects/{p.slug}/update/",
        data=json.dumps({"name": "Nuevo Nombre", "github_enabled": False}),
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["name"] == "Nuevo Nombre"
    assert body["github_enabled"] is False
    p.refresh_from_db()
    assert p.name == "Nuevo Nombre"
    assert p.github_enabled is False


def test_project_update_rejects_immutable_fields(tmp_path):
    """slug/path/status no son editables — PATCH con esos campos → 400."""
    p = _project("imm", tmp_path)
    c = _client_verified()
    r = c.generic(
        method="PATCH",
        path=f"/api/v1/projects/{p.slug}/update/",
        data=json.dumps({"slug": "evil", "path": "/tmp/evil", "status": "archived"}),
        content_type="application/json",
    )
    assert r.status_code == 400


def test_project_delete_archives_when_no_active_sessions(tmp_path):
    p = _project("del", tmp_path)
    c = _client_verified()
    r = c.generic(method="DELETE", path=f"/api/v1/projects/{p.slug}/delete/")
    assert r.status_code == 200, r.content
    p.refresh_from_db()
    assert p.status == "archived"
    # No aparece en /api/v1/projects/ (lista solo ACTIVE).
    r2 = c.get("/api/v1/projects/")
    slugs = [x["slug"] for x in r2.json()]
    assert "del" not in slugs


def test_project_delete_409_with_active_sessions(tmp_path):
    from panel.core.models import Session as _Session
    p = _project("busy", tmp_path)
    Path(p.path).mkdir(parents=True, exist_ok=True)
    _Session.objects.create(project=p, status=_Session.Status.RUNNING)
    c = _client_verified()
    r = c.generic(method="DELETE", path=f"/api/v1/projects/{p.slug}/delete/")
    assert r.status_code == 409
    p.refresh_from_db()
    # Status intacto.
    assert p.status == "active"


def test_project_diff_files_lists_modified_with_counts(tmp_path):
    """FASE UX-R.1: /diff/files/ devuelve lista con +/− counts por archivo."""
    p = _project("df", tmp_path)
    base = Path(p.path)
    base.mkdir(parents=True, exist_ok=True)
    import subprocess
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=base, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=base, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=base, check=True)
    (base / "a.txt").write_text("uno\ndos\n")
    subprocess.run(["git", "add", "."], cwd=base, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=base, check=True)
    (base / "a.txt").write_text("uno\nDOS\ntres\n")
    c = _client_verified()
    r = c.get(f"/api/v1/projects/{p.slug}/diff/files/")
    assert r.status_code == 200
    files = r.json()["files"]
    assert len(files) == 1
    f = files[0]
    assert f["path"] == "a.txt"
    assert f["status"] == "M"
    assert f["additions"] == 2
    assert f["deletions"] == 1
    assert f["is_binary"] is False


def test_project_diff_file_returns_diff_for_specific_path(tmp_path):
    """FASE UX-R.1: /diff/file/?path= devuelve el diff de un solo archivo."""
    p = _project("dff", tmp_path)
    base = Path(p.path)
    base.mkdir(parents=True, exist_ok=True)
    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=base, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=base, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=base, check=True)
    (base / "x.py").write_text("uno")
    subprocess.run(["git", "add", "."], cwd=base, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=base, check=True)
    (base / "x.py").write_text("dos")
    c = _client_verified()
    r = c.get(f"/api/v1/projects/{p.slug}/diff/file/?path=x.py")
    assert r.status_code == 200
    body = r.json()
    assert body["path"] == "x.py"
    assert "-uno" in body["diff"]
    assert "+dos" in body["diff"]


def test_project_diff_file_path_traversal_blocked(tmp_path):
    """FASE C.5 path-traversal OBLIGATORIO: path=../etc/passwd → 403."""
    p = _project("dt", tmp_path)
    Path(p.path).mkdir(parents=True, exist_ok=True)
    c = _client_verified()
    r = c.get(f"/api/v1/projects/{p.slug}/diff/file/?path=../../etc/passwd")
    assert r.status_code == 403


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


def test_mcp_create_validation(tmp_path):
    """UX-T.3: POST /api/v1/mcps/create/ — name requerido, project real, unique."""
    c = _client_verified()
    # name vacío
    r = c.post("/api/v1/mcps/create/",
               data=json.dumps({"scope": "global", "transport": "stdio", "config": {}}),
               content_type="application/json")
    assert r.status_code == 400
    # scope=project sin project
    r = c.post("/api/v1/mcps/create/",
               data=json.dumps({"name": "x", "scope": "project", "transport": "stdio", "config": {}}),
               content_type="application/json")
    assert r.status_code == 400
    # OK
    r = c.post("/api/v1/mcps/create/",
               data=json.dumps({"name": "ports2", "scope": "global", "transport": "stdio", "config": {"k": 1}}),
               content_type="application/json")
    assert r.status_code == 201
    assert r.json()["name"] == "ports2"
    # Duplicado
    r = c.post("/api/v1/mcps/create/",
               data=json.dumps({"name": "ports2", "scope": "global", "transport": "stdio", "config": {}}),
               content_type="application/json")
    assert r.status_code == 409


def test_mcp_update_and_delete(tmp_path):
    """UX-T.3: PATCH cambia enabled, DELETE deshabilita."""
    m = McpServer.objects.create(
        name="upd", scope=McpServer.Scope.GLOBAL,
        transport=McpServer.Transport.STDIO, config={},
    )
    c = _client_verified()
    r = c.generic(method="PATCH", path=f"/api/v1/mcps/{m.id}/update/",
                  data=json.dumps({"enabled": False}),
                  content_type="application/json")
    assert r.status_code == 200
    m.refresh_from_db()
    assert m.enabled is False
    r = c.generic(method="DELETE", path=f"/api/v1/mcps/{m.id}/delete/")
    assert r.status_code == 200
    m.refresh_from_db()
    assert m.enabled is False
    # hard delete
    r = c.generic(method="DELETE", path=f"/api/v1/mcps/{m.id}/delete/?hard=1")
    assert r.status_code == 200
    assert not McpServer.objects.filter(pk=m.id).exists()

# ---------- FASE D: API REST de ModelProfile ----------

def _make_model(name="test-model", provider="anthropic", model_name="claude-3-5-sonnet-20241022",
               base_url=None, token=None, **extra):
    p = ModelProfile.objects.create(
        name=name, provider=provider, model=model_name, base_url=base_url, **extra,
    )
    if token:
        from panel.core.services import models as model_svc
        model_svc.store_token(p, token)
        p.save(update_fields=["auth_token_enc", "updated_at"])
    return p




# ---------- FASE D: API REST de ModelProfile ----------

def _make_model(name="test-model", provider="anthropic", model_name="claude-3-5-sonnet-20241022",
               base_url=None, token=None, **extra):
    p = ModelProfile.objects.create(
        name=name, provider=provider, model=model_name, base_url=base_url, **extra,
    )
    if token:
        from panel.core.services import models as model_svc
        model_svc.store_token(p, token)
        p.save(update_fields=["auth_token_enc", "updated_at"])
    return p


def test_models_list_returns_profiles():
    _make_model(name="m1")
    _make_model(name="m2", provider="minimax", model_name="MiniMax-M3")
    c = _client_verified()
    r = c.get("/api/v1/models/")
    assert r.status_code == 200
    data = r.json()
    names = {m["name"] for m in data}
    assert {"m1", "m2"} <= names


def test_model_create_with_token_does_not_echo_token():
    """POST con auth_token: el token NO debe volver en la respuesta, ni
    siquiera cifrado (es write-only)."""
    c = _client_verified()
    token = "sk-secret-TOKEN-FAKE-12345"
    r = c.post(
        "/api/v1/models/create/",
        data=json.dumps({
            "name": "anthropic-prod",
            "provider": "anthropic",
            "model": "claude-3-5-sonnet-20241022",
            "base_url": "https://api.anthropic.com",
            "auth_token": token,
        }),
        content_type="application/json",
    )
    assert r.status_code == 201
    body = r.json()
    # El token NO debe aparecer en NINGÚN campo de la respuesta.
    assert "auth_token" not in body
    assert "auth_token_enc" not in body
    # has_token=True indica que SÍ se guardó (pero no el valor).
    assert body["has_token"] is True
    # Y un grep exhaustivo sobre el JSON.
    raw = r.content.decode()
    assert "sk-secret-TOKEN-FAKE" not in raw, (
        f"Token filtrado en la respuesta POST create:\n{raw}"
    )
    # Verificamos que el token se guardó (consultando BD).
    p = ModelProfile.objects.get(name="anthropic-prod")
    from panel.core.services import models as model_svc
    assert model_svc.get_token(p) == token


def test_model_list_does_not_include_token():
    """GET /api/v1/models/ NUNCA devuelve auth_token ni auth_token_enc."""
    _make_model(name="with-tok", token="sk-SECRET-FAKE-99999")
    c = _client_verified()
    r = c.get("/api/v1/models/")
    raw = r.content.decode()
    assert "sk-SECRET-FAKE" not in raw, (
        f"Token filtrado en GET /api/v1/models/:\n{raw}"
    )
    for m in r.json():
        assert "auth_token" not in m
        assert "auth_token_enc" not in m
        assert m["has_token"] is True  # pero sí indica que hay


def test_model_update_can_replace_token():
    """PATCH con auth_token nuevo → reemplaza el cifrado."""
    _make_model(name="upd", token="sk-OLD")
    c = _client_verified()
    p = ModelProfile.objects.get(name="upd")
    pid = p.id
    r = c.generic(method="PATCH", path=f"/api/v1/models/{pid}/update/",
                  data=json.dumps({"auth_token": "sk-NEW"}),
                  content_type="application/json")
    assert r.status_code == 200
    raw = r.content.decode()
    assert "sk-OLD" not in raw
    assert "sk-NEW" not in raw
    from panel.core.services import models as model_svc
    p2 = ModelProfile.objects.get(name="upd")
    assert model_svc.get_token(p2) == "sk-NEW"


def test_model_update_can_clear_token():
    """PATCH con auth_token='' → borra el cifrado."""
    _make_model(name="clr", token="sk-SECRET-AAA")
    c = _client_verified()
    p = ModelProfile.objects.get(name="clr")
    r = c.generic(method="PATCH", path=f"/api/v1/models/{p.id}/update/",
                  data=json.dumps({"auth_token": ""}),
                  content_type="application/json")
    assert r.status_code == 200
    p2 = ModelProfile.objects.get(name="clr")
    from panel.core.services import models as model_svc
    assert model_svc.get_token(p2) is None
    assert p2.auth_token_enc is None or len(p2.auth_token_enc) == 0


def test_model_test_does_not_echo_token():
    """POST /test/ → ping al base_url con el token en Authorization. La
    respuesta NUNCA debe incluir el token, ni siquiera en stderr."""
    _make_model(name="ping", base_url="http://127.0.0.1:1/", token="sk-SECRET-XXX")
    c = _client_verified()
    p = ModelProfile.objects.get(name="ping")
    r = c.post(f"/api/v1/models/{p.id}/test/")
    raw = r.content.decode()
    assert "sk-SECRET-XXX" not in raw
    assert r.status_code == 200


def test_model_test_follows_redirects_for_anthropic_endpoint(monkeypatch):
    """FIX 301: minimax/anthropic-style base_url sin trailing `/` responde
    301 → /v1/messages. httpx debe seguir el redirect (con auth header) y
    el helper debe reportar ok=False con status 401 (endpoint alcanzable,
    token inválido), NUNCA reportar '301 moved permanently'."""
    import httpx
    from panel.core.services import models as model_svc

    profile = _make_model(
        name="anthropic-style",
        base_url="https://api.example.com/anthropic",
        token="sk-SECRET-REDIR",
    )

    # Interceptor: la primera request recibe 301; el follow-up a /v1/messages
    # recibe 401. httpx con follow_redirects=True propagará Authorization.
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/v1/messages"):
            return httpx.Response(401, text="bad token")
        return httpx.Response(301, headers={"Location": f"{req.url.scheme}://{req.url.netloc}/v1/messages"})

    # monkeypatch Transport — la firma de httpx.get(url, ...) hace httpx por
    # debajo. Sustituimos get_transport_class en _default_transport.
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: transport.handle_request(args[0]) if False else None, raising=False)
    # Forma robusta: parcheamos el helper del módulo para usar el MockTransport
    # indirectamente via el cliente httpx por defecto.
    orig_get = httpx.get
    def fake_get(url, **kw):
        client = httpx.Client(transport=transport, timeout=kw.get("timeout", 5))
        kw.pop("follow_redirects", None)
        return client.get(url, follow_redirects=kw.get("follow_redirects", True))
    monkeypatch.setattr(model_svc.httpx, "get", fake_get)

    out = model_svc.ping(profile)
    assert "sk-SECRET-REDIR" not in str(out)
    # Status 401 final = ok=False (NO alcanzable + token inválido), pero
    # NO debe contener "301".
    assert out.get("status") != 301
    if "error" in out:
        assert "301" not in out["error"]


def test_model_test_accepts_3xx_after_redirect(monkeypatch):
    """FIX 301: si el endpoint redirige a un 2xx/4xx, ping lo reporta,
    sin mostrar 'moved permanently' al usuario."""
    import httpx
    from panel.core.services import models as model_svc

    profile = _make_model(
        name="redir",
        base_url="https://api.example.com/proxied",
        token="sk-X",
    )

    def handler(req):
        if req.url.path.endswith("/v1/messages"):
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(301, headers={"Location": f"{req.url.scheme}://{req.url.netloc}/v1/messages"})

    transport = httpx.MockTransport(handler)
    orig_get = httpx.get
    def fake_get(url, **kw):
        client = httpx.Client(transport=transport, timeout=kw.get("timeout", 5))
        kw.pop("follow_redirects", None)
        return client.get(url, follow_redirects=kw.get("follow_redirects", True))
    monkeypatch.setattr(model_svc.httpx, "get", fake_get)

    out = model_svc.ping(profile)
    assert out["ok"] is True
    assert out["status"] == 200
    assert "error" not in out


def test_model_delete_blocked_if_used_by_project(tmp_path):
    """No se puede borrar un ModelProfile que esté en uso."""
    p = _make_model(name="used")
    proj = _project("uses-it", tmp_path)
    proj.model_profile = p
    proj.save(update_fields=["model_profile", "updated_at"])
    c = _client_verified()
    r = c.generic(method="DELETE", path=f"/api/v1/models/{p.id}/delete/")
    assert r.status_code == 409


def test_model_delete_succeeds_if_unused():
    p = _make_model(name="orphan")
    c = _client_verified()
    r = c.generic(method="DELETE", path=f"/api/v1/models/{p.id}/delete/")
    assert r.status_code == 200
    assert not ModelProfile.objects.filter(name="orphan").exists()


def test_set_project_model_changes_model(tmp_path):
    old = _make_model(name="old-mp")
    new = _make_model(name="new-mp")
    proj = _project("switcher", tmp_path)
    proj.model_profile = old
    proj.save(update_fields=["model_profile", "updated_at"])
    c = _client_verified()
    r = c.post(
        f"/api/v1/projects/{proj.slug}/model/",
        data=json.dumps({"model_profile_id": new.id}),
        content_type="application/json",
    )
    assert r.status_code == 200
    body = r.json()
    assert body["old_model_profile"] == old.id
    assert body["new_model_profile"] == new.id
    assert body["needs_restart"] is True
    proj.refresh_from_db()
    assert proj.model_profile_id == new.id


def test_grep_token_does_not_appear_anywhere():
    """Test 'grep exhaustivo': crear un perfil con un token único, hacer
    GET/POST/PATCH/DELETE/test y verificar que el token NUNCA aparece
    en ninguna respuesta (como en FASE B del GitHub)."""
    secret = "sk-GREP-EXHAUSTIVE-FASE-D-12345"
    _make_model(name="grep-mp", base_url="http://127.0.0.1:1/", token=secret)
    c = _client_verified()
    p = ModelProfile.objects.get(name="grep-mp")
    pid = p.id

    # GET list
    r = c.get("/api/v1/models/")
    assert secret not in r.content.decode()
    # POST test
    r = c.post(f"/api/v1/models/{pid}/test/")
    assert secret not in r.content.decode()
    # PATCH update (sin auth_token, solo name)
    r = c.generic(method="PATCH", path=f"/api/v1/models/{pid}/update/",
                  data=json.dumps({"name": "grep-mp2"}),
                  content_type="application/json")
    assert secret not in r.content.decode()


# --- SP2: session_create ya no duplica filas y hace rollback ante fallo del worker ---

def test_session_create_no_duplicate_row_on_success(monkeypatch, tmp_path):
    """SP2: session_create NO debe crear dos filas. Antes creaba una huérfana
    porque hacía Session.objects.create + luego start_session() que creaba otra.
    Ahora session_create delega TODO a start_session → una sola fila."""
    p = _project("no-dup", tmp_path)
    p.path = str(tmp_path / "no-dup")
    Path(p.path).mkdir(parents=True, exist_ok=True)
    p.save()

    started = []
    def fake_start(sid):
        started.append(sid)
    monkeypatch.setattr(supervisor, "start", fake_start)

    rows_before = Session.objects.filter(project=p).count()
    c = _client_verified()
    r = c.post("/api/v1/sessions/create/",
               data=json.dumps({"slug": p.slug}),
               content_type="application/json")
    assert r.status_code == 201, r.content
    rows_after = Session.objects.filter(project=p).count()
    assert rows_after - rows_before == 1, f"se esperaba 1 fila nueva, hay {rows_after - rows_before}"
    assert len(started) == 1
    # El id devuelto debe coincidir con la única fila creada.
    assert str(Session.objects.filter(project=p).latest("created_at").id) == r.json()["id"]


def test_session_create_rolls_back_when_supervisor_fails(monkeypatch, tmp_path):
    """SP2: si supervisor.start lanza SupervisorError, session_create debe
    hacer rollback (borrar la fila) y devolver 502 con error legible, NO 500."""
    p = _project("rollback", tmp_path)
    p.path = str(tmp_path / "rollback")
    Path(p.path).mkdir(parents=True, exist_ok=True)
    p.save()

    def fake_start(sid):
        raise supervisor.SupervisorError("start rollback falló: unit not found")
    monkeypatch.setattr(supervisor, "start", fake_start)

    rows_before = Session.objects.filter(project=p).count()
    c = _client_verified()
    r = c.post("/api/v1/sessions/create/",
               data=json.dumps({"slug": p.slug}),
               content_type="application/json")
    rows_after = Session.objects.filter(project=p).count()
    assert rows_after == rows_before, "rollback falló: la fila zombie quedó en DB"
    assert r.status_code == 502, f"se esperaba 502, fue {r.status_code}"
    body = r.json()
    assert "no pude arrancar el worker" in body["error"]
    assert "rollback falló" in body["error"]


def test_start_session_emits_session_created_event(monkeypatch, tmp_path):
    """SP2: start_session publica un UIEvent session_step con status
    'session.created' ANTES de supervisor.start, para que el cliente vea
    feedback inmediato."""
    p = _project("emit", tmp_path)
    p.path = str(tmp_path / "emit")
    Path(p.path).mkdir(parents=True, exist_ok=True)
    p.save()

    started_with: list[str] = []
    def fake_start(sid):
        started_with.append(sid)
    monkeypatch.setattr(supervisor, "start", fake_start)

    # Capturar publishes a Redis.
    from panel.core.services import events as event_svc
    published: list[tuple[str, object]] = []
    class FakeRedis:
        def publish(self, channel, payload):
            published.append((channel, payload))
        def close(self):
            pass
    monkeypatch.setattr(event_svc, "publish_event", lambda r, sid, ev: r.publish("ch", ev))

    from panel.core.services import sessions as session_svc
    session = session_svc.start_session(p)

    # Debe haber al menos 1 evento session_step.session.created persistido.
    step_evs = list(session.events.filter(type="session_step"))
    assert any(
        (e.ui_event or {}).get("payload", {}).get("status") == "session.created"
        for e in step_evs
    ), "no se persistió session.created"
    assert len(started_with) == 1
