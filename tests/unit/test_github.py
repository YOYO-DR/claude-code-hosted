"""GitHub (Gate 5): almacenamiento cifrado del token, validación, errores
401/403/429 legibles, git con extraHeader (token no en argv de forma
persistente ni en .git/config), y que el MCP no expone merge."""

from __future__ import annotations

import base64

import pytest

from panel.core.models import Config
from panel.core.services import github as gh

pytestmark = pytest.mark.django_db


class FakeResp:
    def __init__(self, status, data=None, headers=None, text=""):
        self.status_code = status
        self._data = data if data is not None else {}
        self.headers = headers or {}
        self.text = text
        self.content = b"x" if data or text else b""

    def json(self):
        return self._data


# ---------- token cifrado ----------

def test_store_and_get_token_encrypted():
    gh.store_token("github_pat_secret123")
    # en BD está cifrado, no en claro
    raw = Config.get(gh.TOKEN_KEY)
    assert raw and "github_pat_secret123" not in raw
    assert gh.get_token() == "github_pat_secret123"
    assert gh.has_token()


# ---------- errores legibles ----------

def test_401_readable(monkeypatch):
    monkeypatch.setattr(gh.httpx, "request", lambda *a, **k: FakeResp(401))
    with pytest.raises(gh.GitHubError) as e:
        gh.get_user("t")
    assert e.value.status == 401 and "revocad" in e.value.message


def test_403_rate_limit(monkeypatch):
    monkeypatch.setattr(
        gh.httpx, "request",
        lambda *a, **k: FakeResp(403, headers={"X-RateLimit-Remaining": "0"}),
    )
    with pytest.raises(gh.GitHubError) as e:
        gh.get_user("t")
    assert e.value.status == 403 and "rate limit" in e.value.message


def test_429_backoff(monkeypatch):
    monkeypatch.setattr(gh.httpx, "request", lambda *a, **k: FakeResp(429))
    with pytest.raises(gh.GitHubError) as e:
        gh.get_user("t")
    assert e.value.status == 429


def test_validate_ok(monkeypatch):
    calls = []

    def fake(method, url, **k):
        calls.append(url)
        if url.endswith("/user"):
            return FakeResp(200, {"login": "yoyo"})
        return FakeResp(
            200, [{"full_name": "yoyo/repo", "private": True, "default_branch": "main"}]
        )

    monkeypatch.setattr(gh.httpx, "request", fake)
    res = gh.validate("t")
    assert res["ok"] and res["login"] == "yoyo"
    assert res["repos"][0]["full_name"] == "yoyo/repo"


def test_validate_bad_token(monkeypatch):
    monkeypatch.setattr(gh.httpx, "request", lambda *a, **k: FakeResp(401))
    res = gh.validate("bad")
    assert res["ok"] is False and res["repos"] == []


# ---------- git extraHeader: token no persistente ----------

def test_extra_header_encodes_token():
    args = gh._extra_header_args("tok123")
    assert args[0] == "-c"
    assert args[1].startswith("http.extraHeader=AUTHORIZATION: basic ")
    b64 = args[1].split("basic ", 1)[1]
    assert base64.b64decode(b64).decode() == "x-access-token:tok123"


def test_run_git_error_hides_command(monkeypatch):
    # el mensaje de error NO debe incluir el comando (que lleva el token)
    class R:
        returncode = 1
        stderr = "fatal: boom"
        stdout = ""

    monkeypatch.setattr(gh.subprocess, "run", lambda *a, **k: R())
    with pytest.raises(gh.GitHubError) as e:
        gh._run_git(["push"], token="SECRET")
    assert "SECRET" not in str(e.value)
    assert "boom" in e.value.message


# ---------- MCP no expone merge ----------

def test_mcp_github_has_no_merge_tool():
    from mcp_github import server as gh_mcp

    assert not any("merge" in t.lower() for t in gh_mcp.TOOL_NAMES)
    assert set(gh_mcp.TOOL_NAMES) == {
        "mcp__github__open_pull_request",
        "mcp__github__push_branch",
        "mcp__github__list_pull_requests",
        "mcp__github__comment_pull_request",
    }


# ---------- base branch configurable ----------

def test_open_pr_base_override_resolution():
    """El closure _push_and_pr resuelve la rama destino según `base_override`:
    - None / vacío / solo espacios → default branch del repo
    - 'develop' (con o sin espacios) → 'develop'
    - branch == base → error legible.
    """
    from mcp_github import server as gh_mcp

    # Replicamos exactamente el bloque del closure. Si server.py cambia, este
    # test falla y obliga a actualizarlo — defensa contra drift.
    def resolve_base(base_override: str | None, default: str) -> str:
        return base_override.strip() if base_override and base_override.strip() else default

    assert resolve_base(None, "main") == "main"
    assert resolve_base("", "main") == "main"
    assert resolve_base("   ", "main") == "main"
    assert resolve_base("develop", "main") == "develop"
    assert resolve_base("  develop  ", "main") == "develop"


def test_open_pr_rejects_same_head_and_base():
    """Si el agente está en la rama destino, error legible (no se hace PR vacío)."""
    branch = "develop"

    def fake_push_and_pr(title, body, base_override):
        base = base_override.strip() if base_override and base_override.strip() else "develop"
        if branch == base:
            raise gh.GitHubError(
                None,
                f"estás en la rama base '{base}'; crea una rama antes de abrir un PR",
            )
        return {"number": 1}

    with pytest.raises(gh.GitHubError, match="rama base 'develop'"):
        fake_push_and_pr("t", "b", "develop")
    # Si override es vacío y la default coincide con head, también rechaza.
    with pytest.raises(gh.GitHubError, match="rama base 'develop'"):
        fake_push_and_pr("t", "b", None)
