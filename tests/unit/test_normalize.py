"""FASE B: normalizer SDK → UIEvent v1.

Golden tests basados en fixtures/normalize_v1.json (turno corto:
init + stream deltas + assistant(text+tool_use) + user(tool_result) +
result + thinking_tokens). El golden esperado está en
fixtures/normalize_v1_golden.json.

Si cambias deliberadamente el contrato UIEvent, regenera el golden con
`/tmp/gen_golden.py` (NO a mano; el diff debe ser revisado en el PR).
"""
# ruff: noqa: I001  — claude_agent_sdk tiene "_" y ruff lo trata como local
from __future__ import annotations

import json
from pathlib import Path

from claude_agent_sdk import (  # noqa: I001
    AssistantMessage,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from panel.core.events.normalize import (
    StreamAccumulator,
    UI_EVENT_VERSION,
    normalize_sdk_message,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _load(name: str) -> dict:
    with open(FIXTURES / name) as f:
        return json.load(f)


def _reconstruct(seq: int, etype: str, payload: dict):
    """Reconstruye el mensaje SDK a partir de la serialización cruda."""
    if etype == "system.init":
        return SystemMessage(subtype="init", data=payload.get("data", {}))
    if etype.startswith("stream."):
        return StreamEvent(
            uuid=f"recon-{seq}",
            session_id="fixture-session-uuid",
            event=payload.get("event") or {},
            parent_tool_use_id=payload.get("parent_tool_use_id"),
        )
    if etype == "assistant":
        content = []
        for b in (payload.get("content") or []):
            if b.get("type") == "text":
                content.append(TextBlock(text=b["text"]))
            elif b.get("type") == "tool_use":
                content.append(ToolUseBlock(id=b["id"], name=b["name"], input=b["input"]))
        return AssistantMessage(
            content=content,
            model=payload.get("model") or "MiniMax-M3",
            session_id=payload.get("session_id") or "fixture-session-uuid",
        )
    if etype == "user":
        content = []
        for b in (payload.get("content") or []):
            if b.get("type") == "tool_result":
                content.append(ToolResultBlock(
                    tool_use_id=b["tool_use_id"],
                    content=b["content"],
                    is_error=b.get("is_error", False),
                ))
        return UserMessage(content=content)
    if etype == "result":
        return ResultMessage(
            subtype=payload.get("subtype", "success"),
            duration_ms=payload.get("duration_ms", 0),
            duration_api_ms=payload.get("duration_api_ms", 0),
            is_error=payload.get("is_error", False),
            num_turns=payload.get("num_turns", 0),
            session_id=payload.get("session_id") or "fixture-session-uuid",
            stop_reason=payload.get("stop_reason"),
            total_cost_usd=payload.get("total_cost_usd"),
            result=payload.get("result"),
        )
    if etype == "system.thinking_tokens":
        return SystemMessage(subtype="thinking_tokens", data=payload.get("data", {}))
    return None


# ---------- Golden: el fixture produce exactamente los UIEvent esperados ----------

def test_golden_ui_events_match():
    """El fixture de un turno real (init + stream + assistant + user + result
    + thinking_tokens) produce EXACTAMENTE los UIEvent guardados en el golden."""
    fixture = _load("normalize_v1.json")
    golden = _load("normalize_v1_golden.json")

    acc = StreamAccumulator()
    produced_ui = []
    produced_deltas = []

    for ev_dict in fixture["events"]:
        seq = ev_dict["seq"]
        etype = ev_dict["type"]
        payload = ev_dict["payload"]
        sdk_msg = _reconstruct(seq, etype, payload)
        if sdk_msg is None:
            continue
        if isinstance(sdk_msg, StreamEvent):
            for d in acc.feed_stream(sdk_msg.event or {}):
                d.seq = seq
                d.session_id = "fixture-session-uuid"
                produced_deltas.append({
                    "seq": seq, "kind": d.kind, "payload": d.payload,
                })
        else:
            for ue in normalize_sdk_message(
                sdk_msg, seq=seq, session_id="fixture-session-uuid",
            ):
                produced_ui.append({
                    "seq": seq, "kind": ue.kind, "payload": ue.payload,
                })

    assert produced_ui == golden["ui_events"], (
        f"UIEvent drift:\n  produced: {produced_ui}\n  expected:  {golden['ui_events']}"
    )
    assert produced_deltas == golden["deltas"], (
        f"Delta drift:\n  produced: {produced_deltas}\n  expected:  {golden['deltas']}"
    )


# ---------- Cobertura por kind ----------

def test_system_init_produces_session_status():
    """system.init → kind=session_status con model/tools/mcp_servers/cwd."""
    msg = SystemMessage(subtype="init", data={
        "model": "MiniMax-M3", "tools": ["Bash", "Read"],
        "mcp_servers": [{"name": "ports"}], "cwd": "/srv/projects/x",
    })
    out = normalize_sdk_message(msg, seq=1, session_id="s1")
    assert len(out) == 1
    assert out[0].kind == "session_status"
    assert out[0].payload["status"] == "init"
    assert out[0].payload["model"] == "MiniMax-M3"
    assert "Bash" in out[0].payload["tools"]


def test_system_thinking_tokens_emits_no_uievent():
    """system.thinking_tokens NO genera UIEvent (es telemetría)."""
    msg = SystemMessage(subtype="thinking_tokens", data={"tokens": 1234})
    out = normalize_sdk_message(msg, seq=1, session_id="s1")
    assert out == []


def test_system_compact_boundary_emits_compact():
    """SP12: compact_boundary → divisor `compact` (antes se tragaba)."""
    msg = SystemMessage(subtype="compact_boundary", data={
        "compact_metadata": {"pre_tokens": 150000, "trigger": "auto"},
    })
    out = normalize_sdk_message(msg, seq=1, session_id="s1")
    assert len(out) == 1
    assert out[0].kind == "compact"
    assert out[0].payload["pre_tokens"] == 150000
    assert out[0].payload["trigger"] == "auto"


def test_rate_limit_event_emits_rate_limit():
    """SP12: RateLimitEvent → kind `rate_limit`."""
    from claude_agent_sdk import RateLimitEvent, RateLimitInfo

    info = RateLimitInfo(
        status="allowed_warning", resets_at=None, rate_limit_type="tokens",
        utilization=0.82, overage_status=None, overage_resets_at=None,
        overage_disabled_reason=None, raw={},
    )
    msg = RateLimitEvent(rate_limit_info=info, uuid="u", session_id="s1")
    out = normalize_sdk_message(msg, seq=1, session_id="s1")
    assert len(out) == 1
    assert out[0].kind == "rate_limit"
    assert out[0].payload["utilization"] == 0.82


def test_assistant_server_tool_use_marks_server_tool():
    """SP12: ServerToolUseBlock (web_search, …) → tool_call con server_tool."""
    from claude_agent_sdk import ServerToolUseBlock

    msg = AssistantMessage(
        content=[ServerToolUseBlock(id="st1", name="web_search", input={"q": "x"})],
        model="MiniMax-M3", session_id="s1",
    )
    out = normalize_sdk_message(msg, seq=1, session_id="s1")
    assert len(out) == 1
    assert out[0].kind == "tool_call"
    assert out[0].payload["server_tool"] == "web_search"


def test_assistant_message_error_emits_error():
    """SP12: AssistantMessage.error (auth/billing/…) se surface como `error`."""
    msg = AssistantMessage(
        content=[], model="MiniMax-M3", session_id="s1", error="authentication_failed",
    )
    out = normalize_sdk_message(msg, seq=1, session_id="s1")
    assert len(out) == 1
    assert out[0].kind == "error"
    assert out[0].payload["fatal"] is True


def test_assistant_text_block_emits_agent_text():
    msg = AssistantMessage(
        content=[TextBlock(text="Hola mundo")],
        model="MiniMax-M3",
        session_id="s1",
    )
    out = normalize_sdk_message(msg, seq=1, session_id="s1")
    assert len(out) == 1
    assert out[0].kind == "agent_text"
    assert out[0].payload["text"] == "Hola mundo"
    assert out[0].payload["streaming"] is False


def test_assistant_thinking_block_emits_agent_thinking():
    msg = AssistantMessage(
        content=[ThinkingBlock(thinking="pensando...", signature="sig")],
        model="MiniMax-M3",
        session_id="s1",
    )
    out = normalize_sdk_message(msg, seq=1, session_id="s1")
    assert len(out) == 1
    assert out[0].kind == "agent_thinking"
    assert out[0].payload["text"] == "pensando..."


def test_assistant_tool_use_emits_tool_call():
    msg = AssistantMessage(
        content=[ToolUseBlock(id="t1", name="Bash", input={"command": "ls"})],
        model="MiniMax-M3",
        session_id="s1",
    )
    out = normalize_sdk_message(msg, seq=1, session_id="s1")
    assert len(out) == 1
    assert out[0].kind == "tool_call"
    assert out[0].payload["tool_use_id"] == "t1"
    assert out[0].payload["name"] == "Bash"
    assert out[0].payload["input"] == {"command": "ls"}
    # Sin perm_requests: no se marca awaiting_permission
    assert "awaiting_permission" not in out[0].payload


def test_assistant_tool_use_with_pending_perm_marks_blocking():
    msg = AssistantMessage(
        content=[ToolUseBlock(id="t1", name="Bash", input={"command": "ls"})],
        model="MiniMax-M3",
        session_id="s1",
    )
    perm = {"id": "p1", "tool": "Bash", "input_preview": "ls", "expires_at": "..."}
    out = normalize_sdk_message(
        msg, seq=1, session_id="s1",
        perm_requests={"t1": perm},
    )
    assert out[0].payload["awaiting_permission"] is True


def test_user_tool_result_emits_tool_result():
    msg = UserMessage(content=[
        ToolResultBlock(tool_use_id="t1", content="hello", is_error=False),
    ])
    out = normalize_sdk_message(msg, seq=1, session_id="s1")
    assert len(out) == 1
    assert out[0].kind == "tool_result"
    assert out[0].payload["tool_use_id"] == "t1"
    assert out[0].payload["ok"] is True
    assert out[0].payload["output"] == "hello"


def test_user_tool_result_with_error_marks_not_ok():
    msg = UserMessage(content=[
        ToolResultBlock(tool_use_id="t1", content="boom", is_error=True),
    ])
    out = normalize_sdk_message(msg, seq=1, session_id="s1")
    assert out[0].payload["ok"] is False


def test_result_message_emits_run_result():
    msg = ResultMessage(
        subtype="success",
        duration_ms=1000, duration_api_ms=900,
        is_error=False, num_turns=2, session_id="s1",
        stop_reason="end_turn", total_cost_usd=0.012,
        result="Resumen breve",
    )
    out = normalize_sdk_message(msg, seq=1, session_id="s1")
    assert len(out) == 1
    assert out[0].kind == "run_result"
    assert out[0].payload["ok"] is True
    assert out[0].payload["cost_usd"] == 0.012
    assert out[0].payload["num_turns"] == 2
    assert out[0].payload["summary"] == "Resumen breve"


# ---------- StreamAccumulator ----------

def test_stream_accumulator_text_deltas():
    """3 deltas text_delta en el mismo content_block_index producen 3
    UIEvent streaming=True con el texto acumulado."""
    acc = StreamAccumulator()
    # block_start
    deltas1 = acc.feed_stream({"type": "content_block_start", "index": 0,
                               "content_block": {"type": "text", "text": ""}})
    assert deltas1 == []
    # 3 deltas
    full_deltas = []
    for delta_text in ["Listando ", "el directorio ", "del proyecto…"]:
        ds = acc.feed_stream({"type": "content_block_delta", "index": 0,
                              "delta": {"type": "text_delta", "text": delta_text}})
        for d in ds:
            d.seq = 1
            d.session_id = "s1"
            full_deltas.append(d.payload)
    assert len(full_deltas) == 3
    assert full_deltas[0]["text"] == "Listando "
    assert full_deltas[1]["text"] == "Listando el directorio "
    assert full_deltas[2]["text"] == "Listando el directorio del proyecto…"
    for d in full_deltas:
        assert d["streaming"] is True


def test_stream_accumulator_block_stop_clears_state():
    """content_block_stop descarta el bloque, deltas nuevos con otro índice arrancan de 0."""
    acc = StreamAccumulator()
    acc.feed_stream({"type": "content_block_start", "index": 0,
                     "content_block": {"type": "text", "text": ""}})
    acc.feed_stream({"type": "content_block_delta", "index": 0,
                     "delta": {"type": "text_delta", "text": "abc"}})
    acc.feed_stream({"type": "content_block_stop", "index": 0})
    # Nuevo bloque en índice 1
    acc.feed_stream({"type": "content_block_start", "index": 1,
                     "content_block": {"type": "text", "text": ""}})
    new_deltas = acc.feed_stream({"type": "content_block_delta", "index": 1,
                                   "delta": {"type": "text_delta", "text": "X"}})
    assert len(new_deltas) == 1
    assert new_deltas[0].payload["text"] == "X"


def test_stream_accumulator_ignores_unknown_events():
    """Eventos sin index o sin delta conocido → []."""
    acc = StreamAccumulator()
    assert acc.feed_stream({"type": "message_start"}) == []
    assert acc.feed_stream({"type": "ping"}) == []


# ---------- Defensa ante entradas malformadas ----------

def test_unknown_sdk_message_degrades_to_error():
    """Un mensaje que no encaja en ningún branch produce UIEvent kind=error,
    sin crashear."""
    class WeirdMessage:
        pass
    msg = WeirdMessage()
    out = normalize_sdk_message(msg, seq=1, session_id="s1")
    assert len(out) == 1
    assert out[0].kind == "error"
    assert "WeirdMessage" in out[0].payload["message"]


def test_assistant_with_unknown_block_degrades_to_tool_call_generic():
    """Si llega un bloque que no es Text/Thinking/ToolUse/ToolResult, se
    degrada a tool_call genérico (no se cae)."""
    class UnknownBlock:
        type = "future_block"
        def __init__(self):
            self.id = "x1"
    msg = AssistantMessage(
        content=[UnknownBlock()], model="m", session_id="s1",
    )
    out = normalize_sdk_message(msg, seq=1, session_id="s1")
    assert len(out) == 1
    assert out[0].kind == "tool_call"
    assert out[0].payload["generic"] is True
    assert out[0].payload["name"] == "UnknownBlock"


def test_tool_result_with_list_content_is_coerced_to_string():
    """tool_result.content como lista de dicts {type:text} → string."""
    msg = UserMessage(content=[
        ToolResultBlock(
            tool_use_id="t1",
            content=[{"type": "text", "text": "line 1\n"},
                     {"type": "text", "text": "line 2"}],
        ),
    ])
    out = normalize_sdk_message(msg, seq=1, session_id="s1")
    assert out[0].payload["output"] == "line 1\n\nline 2"


def test_ui_event_version_is_1():
    """El contrato UIEvent está versionado: si subimos a v2, esto rompe."""
    assert UI_EVENT_VERSION == 1