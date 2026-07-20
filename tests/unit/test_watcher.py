"""FASE C.6: watcher de rama git_branch en vivo.

El worker emite un UIEvent `git_branch {branch, dirty}` cuando cambia
la rama activa o el estado dirty del repo del proyecto. Solo se filtra
para tools que mutan (Edit/Write/MultiEdit/Bash con `git`).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from django.utils import timezone

from panel.core.models import (
    ModelProfile,
    PermissionPolicy,
    Project,
    Session,
)
from workers import session_worker

pytestmark = pytest.mark.django_db(transaction=True)


def _git_init(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "a.txt").write_text("uno")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)


def _make_worker_and_session(tmp_path):
    profile = ModelProfile.objects.create(name="p", provider="anthropic", model="m")
    policy = PermissionPolicy.objects.create(name="pol")
    project = Project.objects.create(
        slug="watch", name="Watch", path=str(tmp_path / "watch"),
        model_profile=profile, permission_policy=policy,
    )
    path = tmp_path / "watch"
    path.mkdir(parents=True, exist_ok=True)
    _git_init(path)
    sess = Session.objects.create(project=project, started_at=timezone.now())
    w = session_worker.Worker.__new__(session_worker.Worker)
    w.sid = str(sess.id)
    w._seq = 100
    w._last_git_state = None
    w._stream_acc = None  # no se usa en este test
    # Capturamos publishes de git_branch en una lista.
    w._published_ui: list[dict] = []
    from unittest.mock import patch
    w._patch = patch.object(w, "_redis_publish_ui", side_effect=lambda d: w._published_ui.append(d))
    w._patch.start()
    return w, sess, path


def test_emits_git_branch_when_dirty_changes(tmp_path):
    from claude_agent_sdk import AssistantMessage, ToolUseBlock
    w, sess, path = _make_worker_and_session(tmp_path)
    # Simula que el agente escribió de verdad: modifica el archivo committed
    # para que `git status --porcelain` lo reporte como dirty.
    (path / "a.txt").write_text("dos")
    # Tool mutante (Edit) → debe emitir git_branch con dirty=True.
    msg = AssistantMessage(
        content=[ToolUseBlock(id="t1", name="Edit",
                             input={"file_path": str(path / "a.txt")})],
        model="m", session_id=str(sess.id),
    )
    # Async → la prueba corre con asyncio.
    import asyncio
    asyncio.run(w._maybe_emit_git_branch(sess, msg))
    # Primera emisión (cambio desde "clean" a "dirty").
    assert len(w._published_ui) == 1
    payload = w._published_ui[0]["payload"]
    assert payload["branch"] in ("master", "main")
    assert payload["dirty"] is True


def test_no_emit_when_state_unchanged(tmp_path):
    from claude_agent_sdk import AssistantMessage, ToolUseBlock
    w, sess, path = _make_worker_and_session(tmp_path)
    msg = AssistantMessage(
        content=[ToolUseBlock(id="t1", name="Edit",
                             input={"file_path": str(path / "a.txt")})],
        model="m", session_id=str(sess.id),
    )
    import asyncio
    asyncio.run(w._maybe_emit_git_branch(sess, msg))
    # Pre-pueblo el cache con el mismo estado para forzar no-emit.
    last = w._published_ui[0]["payload"]
    w._last_git_state = f"{last['branch']}|{last['dirty']}"
    asyncio.run(w._maybe_emit_git_branch(sess, msg))
    # Sigue habiendo 1 publicación (no emitió la segunda).
    assert len(w._published_ui) == 1


def test_no_emit_for_readonly_tool(tmp_path):
    from claude_agent_sdk import AssistantMessage, ToolUseBlock
    w, sess, path = _make_worker_and_session(tmp_path)
    # Read NO muta → no emite.
    msg = AssistantMessage(
        content=[ToolUseBlock(id="t1", name="Read",
                             input={"file_path": str(path / "a.txt")})],
        model="m", session_id=str(sess.id),
    )
    import asyncio
    asyncio.run(w._maybe_emit_git_branch(sess, msg))
    assert w._published_ui == []


def test_no_emit_for_bash_without_git(tmp_path):
    """Bash sin "git" no se considera mutante (no toca el repo)."""
    from claude_agent_sdk import AssistantMessage, ToolUseBlock
    w, sess, path = _make_worker_and_session(tmp_path)
    msg = AssistantMessage(
        content=[ToolUseBlock(id="t1", name="Bash",
                             input={"command": "ls -la"})],
        model="m", session_id=str(sess.id),
    )
    import asyncio
    asyncio.run(w._maybe_emit_git_branch(sess, msg))
    assert w._published_ui == []


def test_emits_for_bash_with_git(tmp_path):
    from claude_agent_sdk import AssistantMessage, ToolUseBlock
    w, sess, path = _make_worker_and_session(tmp_path)
    msg = AssistantMessage(
        content=[ToolUseBlock(id="t1", name="Bash",
                             input={"command": "git status"})],
        model="m", session_id=str(sess.id),
    )
    import asyncio
    asyncio.run(w._maybe_emit_git_branch(sess, msg))
    assert len(w._published_ui) == 1


def test_emits_branch_change(tmp_path):
    """Cambiar de rama → emite UIEvent con nueva rama."""
    from claude_agent_sdk import AssistantMessage, ToolUseBlock
    w, sess, path = _make_worker_and_session(tmp_path)
    # Crear segunda rama y checkout (con commit para poder switch).
    subprocess.run(["git", "checkout", "-b", "feature/x"], cwd=path, check=True)
    w._last_git_state = None  # reset para forzar la primera emisión
    msg = AssistantMessage(
        content=[ToolUseBlock(id="t1", name="Bash",
                             input={"command": "git status"})],
        model="m", session_id=str(sess.id),
    )
    import asyncio
    asyncio.run(w._maybe_emit_git_branch(sess, msg))
    assert any(p["payload"]["branch"] == "feature/x" for p in w._published_ui)


def test_no_emit_if_not_git_repo(tmp_path):
    """Si el path no es un repo git, salir silenciosamente (no raise)."""
    from claude_agent_sdk import AssistantMessage, ToolUseBlock
    profile = ModelProfile.objects.create(name="p2", provider="anthropic", model="m")
    policy = PermissionPolicy.objects.create(name="pol2")
    project = Project.objects.create(
        slug="nogit", name="NoGit",
        path=str(tmp_path / "nogit"),  # NO init git
        model_profile=profile, permission_policy=policy,
    )
    (tmp_path / "nogit").mkdir(parents=True, exist_ok=True)
    sess = Session.objects.create(project=project, started_at=timezone.now())
    w = session_worker.Worker.__new__(session_worker.Worker)
    w.sid = str(sess.id)
    w._seq = 1
    w._last_git_state = None
    w._published_ui = []
    from unittest.mock import patch
    patch.object(w, "_redis_publish_ui",
                 side_effect=lambda d: w._published_ui.append(d)).start()
    msg = AssistantMessage(
        content=[ToolUseBlock(id="t1", name="Edit",
                             input={"file_path": str(tmp_path / "nogit" / "x")})],
        model="m", session_id=str(sess.id),
    )
    import asyncio
    # No debe crashear, simplemente no emite.
    asyncio.run(w._maybe_emit_git_branch(sess, msg))
    assert w._published_ui == []