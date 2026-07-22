"""BUGFIX reload (SP12 Parte 1): `_emit` debe persistir TODOS los UIEvent de un
mensaje macro multi-bloque, cada uno con su propio seq. Antes solo persistía
`ui_events[0]`, así que un AssistantMessage con [thinking, text, tool_use]
perdía text y tool_call al recargar (con extended thinking el thinking va
primero → solo sobrevivía "pensando")."""
from __future__ import annotations

import pytest
from channels.db import database_sync_to_async
from claude_agent_sdk import (
    AssistantMessage,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
)

from panel.core.models import (
    Event,
    ModelProfile,
    PermissionPolicy,
    Project,
    Session,
)
from workers import session_worker

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.asyncio]


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


def _worker(session: Session) -> session_worker.Worker:
    from panel.core.events.normalize import StreamAccumulator

    w = session_worker.Worker.__new__(session_worker.Worker)
    w.sid = str(session.id)
    w._seq = 1
    w._session = session
    w._slug = ""
    w._last_git_state = None
    w._stream_acc = StreamAccumulator()

    async def _noop(*a, **k):
        return None

    # Sin Redis ni git en el test unitario.
    w._publish = lambda *a, **k: None  # type: ignore[assignment]
    w._maybe_emit_git_branch = _noop  # type: ignore[assignment]
    return w


async def test_multiblock_message_persists_all_uievents():
    session = await database_sync_to_async(_make_session)()
    w = _worker(session)

    msg = AssistantMessage(
        content=[
            ThinkingBlock(thinking="pensando...", signature="sig"),
            TextBlock(text="Aquí va la respuesta"),
            ToolUseBlock(id="t1", name="Bash", input={"command": "ls"}),
        ],
        model="MiniMax-M3",
        session_id="s1",
    )
    await w._emit(session, msg)

    @database_sync_to_async
    def rows():
        return [
            (e.seq, (e.ui_event or {}).get("kind"))
            for e in Event.objects.filter(session=session).order_by("seq")
        ]

    got = await rows()
    # 3 filas, seqs consecutivos, cada una con su ui_event (no null) en orden.
    assert got == [
        (1, "agent_thinking"),
        (2, "agent_text"),
        (3, "tool_call"),
    ]
