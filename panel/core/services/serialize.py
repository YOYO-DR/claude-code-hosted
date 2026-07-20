"""Serialización de mensajes del Claude Agent SDK a eventos JSON-safe que van a
Postgres y al pubsub. Cubre todos los tipos de mensaje/bloque del SDK
(validado contra claude_agent_sdk 0.2.x). El worker NUNCA serializa aquí el
env del modelo ni input_full de tools sensibles (eso lo controla el worker)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from claude_agent_sdk import (
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

# Nombre de evento por tipo de mensaje del SDK.
_TYPE_NAME = {
    SystemMessage: "system",
    AssistantMessage: "assistant",
    UserMessage: "user",
    ResultMessage: "result",
    StreamEvent: "stream",
}


def event_type(msg: Any) -> str:
    for cls, name in _TYPE_NAME.items():
        if isinstance(msg, cls):
            # El init llega como SystemMessage subtype="init".
            subtype = getattr(msg, "subtype", None)
            if cls is SystemMessage and subtype:
                return f"system.{subtype}"
            # StreamEvent: type concreto del dict interno (message_start, etc.)
            if cls is StreamEvent:
                ev: Any = getattr(msg, "event", None) or {}
                inner = ev.get("type", "unknown")
                return f"stream.{inner}"
            return name
    return type(msg).__name__.lower()


def _block(block: Any) -> dict[str, Any]:
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}
    if isinstance(block, ThinkingBlock):
        return {"type": "thinking", "thinking": block.thinking}
    if isinstance(block, ToolUseBlock):
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    if isinstance(block, ToolResultBlock):
        return {
            "type": "tool_result",
            "tool_use_id": block.tool_use_id,
            "content": _json_safe(block.content),
            "is_error": block.is_error,
        }
    return {"type": type(block).__name__}


def _content(content: Any) -> Any:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return [_block(b) for b in content]
    return _json_safe(content)


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def serialize_message(msg: Any) -> dict[str, Any]:
    """Devuelve el payload JSON-safe del mensaje (sin el `seq`, que lo pone el
    worker al persistir)."""
    if isinstance(msg, SystemMessage):
        return {"subtype": msg.subtype, "data": _json_safe(msg.data)}
    if isinstance(msg, AssistantMessage):
        return {
            "content": _content(msg.content),
            "model": msg.model,
            "session_id": msg.session_id,
        }
    if isinstance(msg, UserMessage):
        return {"content": _content(msg.content)}
    if isinstance(msg, ResultMessage):
        return {
            "subtype": msg.subtype,
            "is_error": msg.is_error,
            "num_turns": msg.num_turns,
            "session_id": msg.session_id,
            "total_cost_usd": msg.total_cost_usd,
            "result": msg.result,
        }
    if isinstance(msg, StreamEvent):
        # El dict interno del evento (Anthropic SSE-style). Lo pasamos entero
        # en `event` para auditoría; el front consume `ui_event` (FASE B).
        return {
            "inner_type": (msg.event or {}).get("type"),
            "event": _json_safe(msg.event),
            "parent_tool_use_id": msg.parent_tool_use_id,
        }
    return {"raw": _json_safe(getattr(msg, "__dict__", str(msg)))}
