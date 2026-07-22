"""Mapeo del callback del worker (§4.2): allow→Allow(updated_input si rewrite),
deny/timeout→Deny(message instructivo). El resto de la lógica de permisos vive
en services/permissions (ver test_permissions)."""

from __future__ import annotations

import pytest
from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

from panel.core.services import permissions as perm_svc
from workers import session_worker

pytestmark = pytest.mark.asyncio


async def _worker():
    w = session_worker.Worker.__new__(session_worker.Worker)
    w.sid = "s"
    w.redis = None  # request_and_wait está mockeado; no se usa
    w._session = object()
    w._slug = ""  # vacío → el hook de puertos se salta

    async def _noop_status(*a, **k):
        return None

    w._set_status = _noop_status  # type: ignore[assignment]
    return w


async def test_allow_without_rewrite(monkeypatch):
    w = await _worker()

    async def fake(*a, **k):
        return "allow", {"command": "ls"}, False, None

    monkeypatch.setattr(perm_svc, "request_and_wait", fake)
    res = await w._can_use_tool("Bash", {"command": "ls"}, None)
    assert isinstance(res, PermissionResultAllow)
    assert res.updated_input is None


async def test_allow_with_rewrite_sets_updated_input(monkeypatch):
    w = await _worker()

    async def fake(*a, **k):
        return "allow", {"file_path": "/x/REWRITTEN.txt"}, True, None

    monkeypatch.setattr(perm_svc, "request_and_wait", fake)
    res = await w._can_use_tool("Write", {"file_path": "/x/ORIGINAL.txt"}, None)
    assert isinstance(res, PermissionResultAllow)
    assert res.updated_input == {"file_path": "/x/REWRITTEN.txt"}


async def test_deny_returns_instructive_message(monkeypatch):
    w = await _worker()

    async def fake(*a, **k):
        return "deny", {}, False, None

    monkeypatch.setattr(perm_svc, "request_and_wait", fake)
    res = await w._can_use_tool("Bash", {}, None)
    assert isinstance(res, PermissionResultDeny)
    assert "denegado" in res.message.lower()


async def test_timeout_returns_timeout_message(monkeypatch):
    w = await _worker()

    async def fake(*a, **k):
        return "timeout", {}, False, None

    monkeypatch.setattr(perm_svc, "request_and_wait", fake)
    res = await w._can_use_tool("Bash", {}, None)
    assert isinstance(res, PermissionResultDeny)
    assert "expiró" in res.message.lower()


class _Rule:
    def __init__(self, tool_name, rule_content):
        self.tool_name = tool_name
        self.rule_content = rule_content


class _Upd:
    def __init__(self, type, behavior, rules):
        self.type = type
        self.behavior = behavior
        self.rules = rules


class _Ctx:
    def __init__(self, suggestions):
        self.suggestions = suggestions


def test_suggested_allow_rules_scopes_bash():
    ctx = _Ctx([_Upd("addRules", "allow", [_Rule("Bash", "git push:*")])])
    assert session_worker._suggested_allow_rules(ctx) == ["Bash(git push:*)"]


def test_suggested_allow_rules_ignores_deny_and_no_content():
    ctx = _Ctx([
        _Upd("addRules", "deny", [_Rule("Bash", "rm:*")]),
        _Upd("addRules", "allow", [_Rule("WebSearch", None)]),
    ])
    assert session_worker._suggested_allow_rules(ctx) == ["WebSearch"]


def test_suggested_allow_rules_empty_ctx():
    assert session_worker._suggested_allow_rules(object()) == []


def test_extract_commands_normalizes_shapes():
    """SP12: get_server_info().commands → [{name, description}] defensivo."""
    info = {"commands": [
        {"name": "/compact", "description": "compacta"},
        {"command": "context", "summary": "muestra contexto"},
        "clear",
    ]}
    assert session_worker._extract_commands(info) == [
        {"name": "compact", "description": "compacta"},
        {"name": "context", "description": "muestra contexto"},
        {"name": "clear", "description": ""},
    ]


def test_extract_commands_empty():
    assert session_worker._extract_commands(None) == []
    assert session_worker._extract_commands({}) == []
