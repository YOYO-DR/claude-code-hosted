"""GitHub (§5). API REST vía httpx + operaciones git con el token inyectado por
`http.extraHeader` (NUNCA se escribe en .git/config). El token vive cifrado en
Config (BD) y se descifra en memoria; jamás se loguea (httpx a WARNING) ni se
pasa en la URL del remoto.

El MCP de agentes (mcp_github) expone abrir PR / push / comentar — NO merge. El
merge duro se refuerza con branch protection del lado de GitHub."""

from __future__ import annotations

import base64
import logging
import subprocess
from typing import Any

import httpx

from panel.core import crypto
from panel.core.models import Config

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

API = "https://api.github.com"
TOKEN_KEY = "github_token_enc"


class GitHubError(RuntimeError):
    def __init__(self, status: int | None, message: str) -> None:
        super().__init__(f"[{status}] {message}")
        self.status = status
        self.message = message


# ---------- token en BD (cifrado) ----------

def get_token() -> str | None:
    enc = Config.get(TOKEN_KEY)
    if not enc:
        return None
    try:
        return crypto.decrypt(enc.encode())
    except Exception:  # noqa: BLE001
        return None


def store_token(token: str) -> None:
    Config.set(TOKEN_KEY, crypto.encrypt(token).decode())


def has_token() -> bool:
    return bool(Config.get(TOKEN_KEY))


# ---------- API REST ----------

def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "claude-code-hosted-panel",
    }


def _request(method: str, path: str, token: str, **json_body) -> Any:
    try:
        resp = httpx.request(
            method, API + path, headers=_headers(token),
            json=json_body or None, timeout=20,
        )
    except httpx.HTTPError as exc:
        raise GitHubError(None, f"error de red: {exc}") from exc
    if resp.status_code == 401:
        raise GitHubError(401, "token inválido o revocado")
    if resp.status_code == 403:
        # rate limit o permiso insuficiente
        if resp.headers.get("X-RateLimit-Remaining") == "0":
            raise GitHubError(403, "rate limit agotado; reintenta más tarde")
        raise GitHubError(403, resp.json().get("message", "prohibido"))
    if resp.status_code == 429:
        raise GitHubError(429, "demasiadas solicitudes; backoff")
    if resp.status_code >= 400:
        try:
            msg = resp.json().get("message", resp.text[:200])
        except Exception:  # noqa: BLE001
            msg = resp.text[:200]
        raise GitHubError(resp.status_code, msg)
    if resp.status_code == 204 or not resp.content:
        return {}
    return resp.json()


def get_user(token: str) -> dict:
    return _request("GET", "/user", token)


def list_repos(token: str, per_page: int = 100) -> list[dict]:
    repos = _request("GET", f"/user/repos?per_page={per_page}&sort=updated", token)
    return [{"full_name": r["full_name"], "private": r["private"],
             "default_branch": r.get("default_branch")} for r in repos]


def get_repo(token: str, full_name: str) -> dict:
    return _request("GET", f"/repos/{full_name}", token)


def check_push_access(token: str, full_name: str) -> tuple[bool, str]:
    """Devuelve (ok, mensaje) sobre los permisos de push del PAT en el repo.

    D13: el operador puede crear un proyecto apuntando a un repo público
    fuera del scope del token (fine-grained con `public_access: read` o
    classic sin `public_repo`). El clone puede pasar (read-only basta) pero
    `git push`/`create_pull` fallarán con 403. Esta función mira la rama
    `permissions` de la respuesta de `/repos/{owner}/{repo}` y devuelve:
      - (True, "ok") si push/pull/admin es True
      - (False, "<razón legible>") si no hay push o el repo es inalcanzable
    """
    try:
        repo = get_repo(token, full_name)
    except GitHubError as exc:
        code = exc.status or 0
        if code == 404:
            return False, "GitHub devolvió 404: repo no existe o el PAT no tiene acceso"
        if code == 403:
            return False, f"GitHub devolvió 403 al inspeccionar el repo: {exc}"
        return False, f"no se pudo inspeccionar el repo: {exc}"
    perms = repo.get("permissions") or {}
    if perms.get("push") or perms.get("maintain") or perms.get("admin"):
        return True, "ok"
    # No push. Razones típicas legibles:
    if repo.get("private"):
        return False, (
            "el PAT no tiene permisos de push sobre este repo privado. "
            "Regenera el token con acceso a este repo."
        )
    return False, (
        "el PAT no tiene push sobre este repo público (típico: fine-grained "
        "con solo `public_access: read`, o classic sin scope `public_repo`). "
        "El clone funcionará pero `git push` y abrir PR fallarán con 403."
    )


def default_branch(token: str, full_name: str) -> str:
    return get_repo(token, full_name).get("default_branch", "main")


def create_pull(
    token: str, full_name: str, *, title: str, head: str, base: str, body: str = ""
) -> dict:
    return _request(
        "POST", f"/repos/{full_name}/pulls", token,
        title=title, head=head, base=base, body=body,
    )


def list_pulls(token: str, full_name: str, state: str = "open") -> list[dict]:
    pulls = _request("GET", f"/repos/{full_name}/pulls?state={state}&per_page=30", token)
    return [{"number": p["number"], "title": p["title"], "state": p["state"],
             "html_url": p["html_url"], "head": p["head"]["ref"]} for p in pulls]


def comment_issue(token: str, full_name: str, number: int, body: str) -> dict:
    return _request("POST", f"/repos/{full_name}/issues/{number}/comments", token, body=body)


def validate(token: str) -> dict:
    """Valida el token para la UI: autentica, lista repos. Devuelve
    {ok, login, repos, error}."""
    try:
        user = get_user(token)
    except GitHubError as exc:
        return {"ok": False, "error": exc.message, "login": None, "repos": []}
    try:
        repos = list_repos(token)
    except GitHubError as exc:
        return {"ok": True, "login": user.get("login"), "repos": [],
                "error": f"repos: {exc.message}"}
    return {"ok": True, "login": user.get("login"), "repos": repos, "error": None}


# ---------- git con token efímero (extraHeader, no .git/config) ----------

def _extra_header_args(token: str) -> list[str]:
    b64 = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    # -c es por-comando: NO se persiste en .git/config.
    return ["-c", f"http.extraHeader=AUTHORIZATION: basic {b64}"]


def _run_git(
    args: list[str], token: str | None = None, cwd: str | None = None
) -> subprocess.CompletedProcess:
    cmd = ["git"]
    if token:
        cmd += _extra_header_args(token)
    cmd += args
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if res.returncode != 0:
        # No incluir el comando (lleva el header) en el error.
        raise GitHubError(None, f"git {args[0]} falló: {res.stderr.strip()[:300]}")
    return res


def clone(token: str, full_name: str, dest: str) -> None:
    url = f"https://github.com/{full_name}.git"
    _run_git(["clone", url, dest], token=token)


def create_branch(dest: str, branch: str, base: str | None = None) -> None:
    if base:
        _run_git(["checkout", base], cwd=dest)
    _run_git(["checkout", "-B", branch], cwd=dest)


def push(token: str, dest: str, branch: str) -> None:
    _run_git(["push", "-u", "origin", branch], token=token, cwd=dest)


def current_branch(dest: str) -> str:
    return _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=dest).stdout.strip()
