"""MCP de GitHub para agentes (§5.3). SDK MCP **in-process** (como el de puertos,
D9): el token vive en memoria del worker (desde la BD cifrada), nunca en disco
del proyecto. Ligado al `github_repo` del proyecto: el agente solo opera sobre SU
repo. Expone abrir PR / push / listar / comentar — **NO merge** (así los agentes
no pueden mergear aunque el token pueda; el candado duro es branch protection)."""

from __future__ import annotations

import json

from asgiref.sync import sync_to_async
from claude_agent_sdk import create_sdk_mcp_server, tool

from panel.core.services import github as gh


def _ok(data: object) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}]}


def _err(msg: str) -> dict:
    return {"content": [{"type": "text", "text": msg}], "is_error": True}


def build_server(repo_full_name: str, dest: str, token: str):
    """MCP 'github' ligado a un repo/dir/token concretos."""

    def _push_and_pr(title: str, body: str) -> dict:
        branch = gh.current_branch(dest)
        gh.push(token, dest, branch)
        base = gh.default_branch(token, repo_full_name)
        if branch == base:
            raise gh.GitHubError(None, f"estás en la rama base '{base}'; crea una rama antes")
        pr = gh.create_pull(token, repo_full_name, title=title, head=branch, base=base, body=body)
        return {"number": pr["number"], "url": pr["html_url"], "head": branch, "base": base}

    d_pr = "Hace push de tu rama actual y abre un Pull Request en el repo del proyecto."
    d_push = "Hace push de tu rama actual al remoto (sin abrir PR)."
    d_list = "Lista los Pull Requests abiertos del repo del proyecto."
    d_comment = "Comenta en un Pull Request del repo del proyecto."

    @tool("open_pull_request", d_pr, {"title": str, "body": str})
    async def open_pull_request(args: dict) -> dict:
        title = args.get("title", "").strip() or "Cambios del agente"
        try:
            data = await sync_to_async(_push_and_pr)(title, args.get("body", ""))
        except Exception as exc:  # noqa: BLE001
            return _err(f"No se pudo abrir el PR: {exc}")
        return _ok(data)

    @tool("push_branch", d_push, {})
    async def push_branch(args: dict) -> dict:
        try:
            branch = await sync_to_async(gh.current_branch)(dest)
            await sync_to_async(gh.push)(token, dest, branch)
        except Exception as exc:  # noqa: BLE001
            return _err(f"No se pudo hacer push: {exc}")
        return _ok({"pushed": branch})

    @tool("list_pull_requests", d_list, {})
    async def list_pull_requests(args: dict) -> dict:
        try:
            pulls = await sync_to_async(gh.list_pulls)(token, repo_full_name)
        except Exception as exc:  # noqa: BLE001
            return _err(f"No se pudo listar: {exc}")
        return _ok(pulls)

    @tool("comment_pull_request", d_comment, {"number": int, "body": str})
    async def comment_pull_request(args: dict) -> dict:
        try:
            await sync_to_async(gh.comment_issue)(
                token, repo_full_name, int(args.get("number", 0)), args.get("body", "")
            )
        except Exception as exc:  # noqa: BLE001
            return _err(f"No se pudo comentar: {exc}")
        return _ok({"ok": True})

    return create_sdk_mcp_server(
        name="github", version="1.0.0",
        tools=[open_pull_request, push_branch, list_pull_requests, comment_pull_request],
    )


TOOL_NAMES = [
    "mcp__github__open_pull_request",
    "mcp__github__push_branch",
    "mcp__github__list_pull_requests",
    "mcp__github__comment_pull_request",
]
