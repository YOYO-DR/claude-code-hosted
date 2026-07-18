"""Contratos del bus Redis (§4.1). Claves y helpers compartidos por el worker
y el panel. Regla: Postgres primero, Redis después."""

from __future__ import annotations


def key_in(sid: str) -> str:
    return f"session:{sid}:in"


def key_out(sid: str) -> str:
    return f"session:{sid}:out"


def key_perm(sid: str) -> str:
    return f"session:{sid}:perm"


def key_answer(uuid: str) -> str:
    return f"perm:{uuid}:answer"


def key_perm_resolved() -> str:
    # Canal global: el worker publica {request_id, outcome} al resolver un
    # permiso; el tg_bridge edita el mensaje de Telegram correspondiente.
    return "perm:resolved"


def key_heartbeat(sid: str) -> str:
    return f"worker:{sid}:heartbeat"


HEARTBEAT_TTL = 15  # segundos; el panel marca crashed si expira
ANSWER_TTL = 900  # segundos (SET NX EX)
