"""MCP de GitHub para agentes (§5.3). SDK MCP **in-process** (como el de puertos,
D9): el token vive en memoria del worker (desde la BD cifrada), nunca en disco
del proyecto. Ligado al `github_repo` del proyecto: el agente solo opera sobre SU
repo. Expone abrir PR / push / listar / comentar — **NO merge** (así los agentes
no pueden mergear aunque el token pueda; el candado duro es branch protection).

`open_pull_request` acepta un parámetro opcional `base` para indicar la rama
destino del PR (ej. `develop`). Si se omite, se usa la rama por defecto del repo
(NO siempre es `main` — la consultamos vía la API). Esto permite PRs de revisión
contra `develop` sin que el operador tenga que tocar nada."""

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

    def _push_and_pr(title: str, body: str, base_override: str | None) -> dict:
        branch = gh.current_branch(dest)
        gh.push(token, dest, branch)
        # Si el agente no indicó base, usamos la rama por defecto del repo
        # (puede ser `main`, `master`, `develop`, la que sea — la consultamos
        # vía /repos/:owner/:repo para no asumir).
        base = base_override.strip() if base_override and base_override.strip() else gh.default_branch(token, repo_full_name)
        if branch == base:
            raise gh.GitHubError(
                None,
                f"estás en la rama base '{base}'; crea una rama antes de abrir un PR",
            )
        pr = gh.create_pull(token, repo_full_name, title=title, head=branch, base=base, body=body)
        return {"number": pr["number"], "url": pr["html_url"], "head": branch, "base": base}

    def _pull_branch(source: str, into: str | None) -> dict:
        # 1) Trae la rama del remoto (fetch aislado para separar errores).
        gh._run_git(["fetch", "origin", source], token=token, cwd=dest)
        # 2) Decide rama destino del merge.
        #    - into vacío / None → rama actual (NO se cambia).
        #    - into == rama actual → no-op (skip checkout).
        #    - into != rama actual → checkout primero.
        target = into.strip() if into and into.strip() else None
        if target:
            current = gh.current_branch(dest)
            if target != current:
                gh._run_git(["checkout", target], cwd=dest)
        # 3) Merge de origin/<source> en la rama actual (post-checkout).
        #    Usamos `git merge` en vez de `git pull` porque ya hicimos fetch
        #    arriba y queremos control fino sobre el mensaje de salida.
        merge_res = gh._run_git(["merge", f"origin/{source}"], cwd=dest)
        sha = gh._run_git(["rev-parse", "--short", "HEAD"], cwd=dest).stdout.strip()
        branch = gh.current_branch(dest)
        return {"branch": branch, "head": sha, "source": source, "summary": merge_res.stdout.strip()}

    d_pr = (
        "Hace push de tu rama actual y abre un Pull Request en el repo del proyecto. "
        "Acepta `base` (string, opcional) para indicar la rama destino del PR — "
        "ej. 'develop' para revisión previa al merge. Si se omite, usa la rama "
        "por defecto del repo."
    )
    d_push = "Hace push de tu rama actual al remoto (sin abrir PR)."
    d_pull = (
        "Trae cambios del remoto sin cambiar tu rama actual (por defecto). "
        "Acepta `source` (rama del remoto a traer, REQUERIDO) e `into` (rama local "
        "destino, OPCIONAL). Si `into` se omite, hace `git fetch origin <source>` "
        "y merge `origin/<source>` en la rama actual — útil para 'estoy en "
        "feat/auth, tráeme los últimos cambios de develop y sigo aquí'. Si "
        "`into` se pasa, hace checkout a esa rama antes del merge (cuando "
        "corresponda). Si hay conflictos locales, falla con un error legible "
        "(no se sobreescriben cambios sin conflicto explícito)."
    )
    d_list = "Lista los Pull Requests abiertos del repo del proyecto."
    d_comment = "Comenta en un Pull Request del repo del proyecto."

    @tool("open_pull_request", d_pr, {"title": str, "body": str, "base": str})
    async def open_pull_request(args: dict) -> dict:
        title = args.get("title", "").strip() or "Cambios del agente"
        try:
            data = await sync_to_async(_push_and_pr)(
                title, args.get("body", ""), args.get("base"),
            )
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

    @tool("pull_branch", d_pull, {"source": str, "into": str})
    async def pull_branch(args: dict) -> dict:
        source = (args.get("source") or "").strip()
        if not source:
            return _err("'source' es requerido (rama del remoto a traer)")
        # Defensa contra inyecciones en nombres de rama. Si pasa, git igual
        # lo rechazaría, pero cortamos en seco con un mensaje legible.
        bad = (" ", "\t", "\n", "~", "^", ":", "?", "*", "[", "\\")
        if any(c in source for c in bad):
            return _err(f"nombre de rama inválido en 'source': {source!r}")
        into = args.get("into")
        if isinstance(into, str):
            if any(c in into for c in bad):
                return _err(f"nombre de rama inválido en 'into': {into!r}")
        try:
            data = await sync_to_async(_pull_branch)(source, into)
        except Exception as exc:  # noqa: BLE001
            return _err(f"No se pudo hacer pull: {exc}")
        return _ok(data)

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
        tools=[
            open_pull_request, push_branch, pull_branch,
            list_pull_requests, comment_pull_request,
        ],
    )


TOOL_NAMES = [
    "mcp__github__open_pull_request",
    "mcp__github__push_branch",
    "mcp__github__pull_branch",
    "mcp__github__list_pull_requests",
    "mcp__github__comment_pull_request",
]
