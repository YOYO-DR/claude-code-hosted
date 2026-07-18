"""Serialización de todos los tipos de mensaje del SDK a eventos JSON-safe."""

import json

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

from panel.core.services.serialize import event_type, serialize_message


def _json_roundtrip(payload):
    # Debe ser serializable a JSON sin errores (va a JSONField y a pubsub).
    return json.loads(json.dumps(payload))


def test_system_init():
    msg = SystemMessage(subtype="init", data={"session_id": "s1", "model": "claude-x"})
    assert event_type(msg) == "system.init"
    p = serialize_message(msg)
    assert p["data"]["session_id"] == "s1"
    _json_roundtrip(p)


def test_assistant_with_all_block_types():
    msg = AssistantMessage(
        content=[
            TextBlock(text="hola"),
            ThinkingBlock(thinking="pienso", signature="sig"),
            ToolUseBlock(id="t1", name="Bash", input={"command": "ls"}),
        ],
        model="claude-x",
    )
    assert event_type(msg) == "assistant"
    p = serialize_message(msg)
    kinds = [b["type"] for b in p["content"]]
    assert kinds == ["text", "thinking", "tool_use"]
    assert p["content"][2]["name"] == "Bash"
    _json_roundtrip(p)


def test_user_with_tool_result():
    msg = UserMessage(content=[ToolResultBlock(tool_use_id="t1", content="ok", is_error=False)])
    assert event_type(msg) == "user"
    p = serialize_message(msg)
    assert p["content"][0]["type"] == "tool_result"
    _json_roundtrip(p)


def test_result_message_cost():
    msg = ResultMessage(
        subtype="success",
        duration_ms=10,
        duration_api_ms=5,
        is_error=False,
        num_turns=1,
        session_id="s1",
        total_cost_usd=0.0123,
        result="listo",
    )
    assert event_type(msg) == "result"
    p = serialize_message(msg)
    assert p["total_cost_usd"] == 0.0123
    assert p["session_id"] == "s1"
    _json_roundtrip(p)


def test_string_content_assistant():
    msg = AssistantMessage(content="texto plano", model="m")
    p = serialize_message(msg)
    assert p["content"] == "texto plano"
    _json_roundtrip(p)
