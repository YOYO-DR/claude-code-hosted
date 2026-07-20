"""Normalizador de eventos del Claude Agent SDK → UIEvent v1 (FASE B).

Por qué existe: el chat actual vuelca el evento crudo (`[994] result: {...}`,
`system.thinking_tokens: {...}`), por eso se ve como un log. OpenHands se ve
bien porque clasifica cada evento y lo renderiza con un componente distinto.
Esa clasificación la hacemos en el backend con este normalizador, NO en el
front — el front consume un discriminated union estable (UIEvent) y decide
la tarjeta visual por `kind`.

El SDK 0.2.x expone AssistantMessage / UserMessage / SystemMessage /
ResultMessage como mensajes "macro", y StreamEvent con `event` siendo un
dict con la estructura de la API de Anthropic (`message_start`,
`content_block_start`, `content_block_delta`, `message_delta`,
`message_stop`). Este módulo:

1. Mantiene un dispatcher principal por tipo de mensaje del SDK → produce 1+
   UIEvent (un assistant con 3 bloques text + tool_use + tool_result genera
   3 UIEvent por el assistant, más 1 por el tool_result si viniera en un
   user message).
2. Acumula los `stream_event` (deltas) en el bloque `agent_text` con
   `streaming=true` para soportar el efecto "escribiendo…" en el chat.
3. Devuelve UIEvent desconocido como `error` (no crashea; degrada a genérico).

Contrato UIEvent v1:
  { "v": 1, "seq": <int>, "session_id": "<uuid>", "ts": "<iso>",
    "kind": "<kind>", ...payload por kind... }

Kinds (ver MIGRATION1 §3.2):
  agent_text, agent_thinking, tool_call, tool_result,
  permission_request, permission_resolved, run_result,
  session_status, git_branch, error

Persistencia dual (DUAL_WRITE): el evento crudo (Event.payload) sigue
intacto para auditoría/replay; el UIEvent vive en Event.ui_event (nullable).
El front consume `ui_event` cuando está, cae al crudo si es None (backfill
no rompe la UI).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

UI_EVENT_VERSION = 1


# ---------- UIEvent v1 (discriminated union por `kind`) ----------

@dataclass
class UIEvent:
    """Forma estable que consume el front (v1)."""
    v: int
    seq: int
    session_id: str
    ts: str
    kind: str
    # payload por kind (solo se rellena el del kind activo)
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _base(seq: int, session_id: str | None) -> UIEvent:
    return UIEvent(
        v=UI_EVENT_VERSION,
        seq=seq,
        session_id=str(session_id) if session_id else "",
        ts=datetime.now(UTC).isoformat(),
        kind="",
        payload={},
    )


# ---------- Normalizer principal ----------

class StreamAccumulator:
    """Acumula deltas de streaming por `tool_use_id` (text/thinking) para
    soportar el efecto "escribiendo…" del chat. Mantiene el último bloque
    parcial por `content_block_index` (los stream_event del SDK traen índice).

    El state vive en el worker (memoria del proceso). Si el worker muere, los
    deltas pendientes se pierden — el próximo UIEvent completo del SDK ya
    trae el texto final, así que no hay pérdida visible al usuario.
    """

    def __init__(self) -> None:
        # content_block_index -> {kind: "text"|"thinking", text: str}
        self._blocks: dict[int, dict[str, Any]] = {}

    def feed_stream(self, ev: dict[str, Any]) -> list[UIEvent]:
        """Procesa un StreamEvent.event (dict estilo Anthropic) y devuelve 0+
        UIEvent con streaming=True (uno por cada delta de un bloque conocido).
        Si el evento no es un delta de texto/thinking, devuelve [].
        """
        kind = ev.get("type")
        out: list[UIEvent] = []
        if kind == "content_block_start":
            idx = ev.get("index")
            block = (ev.get("content_block") or {})
            btype = block.get("type")
            if idx is not None and btype in ("text", "thinking"):
                self._blocks[idx] = {"kind": btype, "text": ""}
            # tool_use start también puede llegar aquí (input vacío al inicio)
        elif kind == "content_block_delta":
            idx = ev.get("index")
            delta = ev.get("delta") or {}
            if idx is None or idx not in self._blocks:
                return out
            block = self._blocks[idx]
            if block["kind"] == "text" and delta.get("type") == "text_delta":
                block["text"] += delta.get("text", "")
                ue = _base(seq=0, session_id="")
                ue.kind = "agent_text"
                ue.payload = {"text": block["text"], "streaming": True}
                out.append(ue)
            elif block["kind"] == "thinking" and delta.get("type") in (
                "thinking_delta", "signature_delta"
            ):
                if delta.get("type") == "thinking_delta":
                    block["text"] += delta.get("thinking", "")
                ue = _base(seq=0, session_id="")
                ue.kind = "agent_thinking"
                ue.payload = {"text": block["text"], "streaming": True}
                out.append(ue)
        elif kind == "content_block_stop":
            idx = ev.get("index")
            if idx is not None:
                self._blocks.pop(idx, None)
        return out


def normalize_sdk_message(
    msg: Any,
    *,
    seq: int,
    session_id: str,
    perm_requests: dict[str, dict] | None = None,
) -> list[UIEvent]:
    """Convierte un mensaje del SDK en 0+ UIEvent. `perm_requests` es el mapa
    opcional tool_use_id → {id, tool, input_preview, expires_at} para los
    UIEvent `permission_request` que el worker va a publicar a continuación
    (los tool_call no son aprobables por sí solos: solo cuando el worker
    crea la PermissionRequest y el front recibe el broadcast de `perm`).
    """
    out: list[UIEvent] = []
    perm_requests = perm_requests or {}

    if isinstance(msg, SystemMessage):
        st = msg.subtype
        if st == "init":
            out.append(_ev(seq, session_id, "session_status", {
                "status": "init",
                "model": (msg.data or {}).get("model"),
                "tools": (msg.data or {}).get("tools") or [],
                "mcp_servers": (msg.data or {}).get("mcp_servers") or [],
                "cwd": (msg.data or {}).get("cwd"),
            }))
        elif st in ("thinking_tokens", "compact_boundary"):
            # Telemetría/estado: NO burbuja. Se persiste como crudo pero no
            # emite UIEvent (alimenta métricas fuera de banda).
            return out
        else:
            out.append(_ev(seq, session_id, "session_status", {
                "status": st, "data": msg.data or {},
            }))

    elif isinstance(msg, AssistantMessage):
        # Un assistant puede traer varios bloques (text + thinking + tool_use).
        for block in msg.content or []:
            if isinstance(block, TextBlock):
                out.append(_ev(seq, session_id, "agent_text", {
                    "text": block.text, "streaming": False,
                }))
            elif isinstance(block, ThinkingBlock):
                out.append(_ev(seq, session_id, "agent_thinking", {
                    "text": block.thinking,
                }))
            elif isinstance(block, ToolUseBlock):
                payload: dict[str, Any] = {
                    "tool_use_id": block.id,
                    "name": block.name,
                    "input": _json_safe(block.input),
                }
                # Si el worker tiene el mapa, lo marcamos como pendiente de
                # aprobación (el front sabrá que es bloqueante si ve luego
                # un permission_request con el mismo tool_use_id).
                req = perm_requests.get(block.id)
                if req:
                    payload["awaiting_permission"] = True
                out.append(_ev(seq, session_id, "tool_call", payload))
            elif isinstance(block, ToolResultBlock):
                # Algunos SDK emiten tool_result dentro de assistant (raro).
                out.append(_ev(seq, session_id, "tool_result", {
                    "tool_use_id": block.tool_use_id,
                    "ok": not block.is_error,
                    "output": _coerce_output(block.content),
                    "truncated": False,
                }))
            else:
                # Bloque desconocido: degradar a tool_call genérico.
                out.append(_ev(seq, session_id, "tool_call", {
                    "tool_use_id": getattr(block, "id", ""),
                    "name": type(block).__name__,
                    "input": {},
                    "generic": True,
                }))

    elif isinstance(msg, UserMessage):
        # El user message casi siempre trae el tool_result.
        content = msg.content
        if isinstance(content, str):
            out.append(_ev(seq, session_id, "error", {
                "message": content, "fatal": False,
            }))
        else:
            for block in content or []:
                if isinstance(block, ToolResultBlock):
                    out.append(_ev(seq, session_id, "tool_result", {
                        "tool_use_id": block.tool_use_id,
                        "ok": not block.is_error,
                        "output": _coerce_output(block.content),
                        "truncated": False,
                    }))
                elif isinstance(block, TextBlock):
                    # Mensaje de usuario (raro: el front lo manda por :in).
                    out.append(_ev(seq, session_id, "agent_text", {
                        "text": block.text, "streaming": False,
                        "from_user": True,
                    }))
                else:
                    out.append(_ev(seq, session_id, "error", {
                        "message": f"user block desconocido: {type(block).__name__}",
                        "fatal": False,
                    }))

    elif isinstance(msg, ResultMessage):
        out.append(_ev(seq, session_id, "run_result", {
            "ok": not msg.is_error,
            "cost_usd": msg.total_cost_usd,
            "num_turns": msg.num_turns,
            "summary": msg.result or "",
            "duration_ms": msg.duration_ms,
            "stop_reason": msg.stop_reason,
        }))

    else:
        # Mensaje desconocido: degradar, nunca crashear.
        out.append(_ev(seq, session_id, "error", {
            "message": f"sdk msg no reconocido: {type(msg).__name__}",
            "fatal": False,
        }))

    return out


# ---------- Helpers ----------

def _ev(seq: int, session_id: str, kind: str, payload: dict) -> UIEvent:
    e = _base(seq, session_id)
    e.kind = kind
    e.payload = payload
    return e


def _coerce_output(content: Any) -> Any:
    """tool_result.content puede ser str, list de dicts {"type":"text",...}
    o None. Lo devolvemos como JSON-safe y razonablemente legible."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # Aplanar bloques text; dejar metadatos si los hay.
        parts = []
        for b in content:
            if isinstance(b, dict):
                if b.get("type") == "text":
                    parts.append(b.get("text", ""))
                else:
                    parts.append(b)
            else:
                parts.append(str(b))
        # Si solo hay strings, los unimos; si hay dicts, devolvemos la lista.
        if all(isinstance(p, str) for p in parts):
            return "\n".join(parts)
        return parts
    return _json_safe(content)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return str(value)