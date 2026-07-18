"""MCP de puertos (§4.5). SDK MCP **in-process**: lo monta el worker vía
`ClaudeAgentOptions.mcp_servers`, NO como servidor stdio en .mcp.json — así los
tokens/creds de Postgres nunca tocan el disco del proyecto (que el agente puede
leer). El slug del proyecto es de confianza: lo fija el worker al construir el
server, no el agente.

Herramientas: allocate_port(purpose), list_ports(), release_port(port)."""

from __future__ import annotations

import json

from asgiref.sync import sync_to_async
from claude_agent_sdk import create_sdk_mcp_server, tool

from panel.core.services import ports as ports_svc


def _ok(data: object) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}]}


def _err(msg: str) -> dict:
    return {"content": [{"type": "text", "text": msg}], "is_error": True}


def build_server(slug: str, session_id: str | None = None):
    """Crea el SDK MCP server 'ports' ligado a un proyecto/sesión concretos."""

    _alloc_desc = "Reserva un puerto libre (20000-29999) para exponer un servicio de este proyecto."
    _list_desc = "Lista los puertos actualmente asignados (de todos los proyectos)."
    _rel_desc = "Libera un puerto de ESTE proyecto al desmontar el servicio."

    @tool("allocate_port", _alloc_desc, {"purpose": str})
    async def allocate_port(args: dict) -> dict:
        try:
            port = await sync_to_async(ports_svc.allocate)(
                slug, args.get("purpose", ""), session_id
            )
        except Exception as exc:  # noqa: BLE001 — cualquier fallo (incl. DB) va limpio al agente
            return _err(f"No se pudo asignar puerto: {exc}")
        return _ok({"port": port})

    @tool("list_ports", _list_desc, {})
    async def list_ports(args: dict) -> dict:
        try:
            rows = await sync_to_async(ports_svc.list_ports)()
        except Exception as exc:  # noqa: BLE001
            return _err(f"No se pudo listar: {exc}")
        return _ok(rows)

    @tool("release_port", _rel_desc, {"port": int})
    async def release_port(args: dict) -> dict:
        try:
            ok = await sync_to_async(ports_svc.release)(slug, int(args.get("port", 0)))
        except Exception as exc:  # noqa: BLE001
            return _err(f"No se pudo liberar: {exc}")
        if not ok:
            return _err("Ese puerto no es de tu proyecto o no está activo; no se liberó.")
        return _ok({"ok": True})

    return create_sdk_mcp_server(
        name="ports", version="1.0.0", tools=[allocate_port, list_ports, release_port]
    )


# Nombres de las tools tal como las ve el agente (para allowed_tools).
TOOL_NAMES = ["mcp__ports__allocate_port", "mcp__ports__list_ports", "mcp__ports__release_port"]
