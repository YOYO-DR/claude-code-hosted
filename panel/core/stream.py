"""Lógica pura de reconexión sin duplicados (§4.1/§4.4), separada del consumer
async para poder property-testearla. La UI se reconecta con last_seq: recibe
el backlog (seq>last_seq) y luego el live; nada se duplica ni se pierde."""

from __future__ import annotations

from collections.abc import Iterable


def backlog_seqs(persisted: Iterable[int], last_seq: int) -> list[int]:
    """Seqs a mandar como backlog: > last_seq, ordenados y únicos."""
    return sorted({s for s in persisted if s > last_seq})


class SeqDedup:
    """Filtra el stream live para que nunca se emita un seq ya emitido (por el
    backlog o por un mensaje pubsub duplicado). Monótono estricto."""

    def __init__(self, last_seq: int) -> None:
        self._last_sent = last_seq

    def should_forward(self, seq: int) -> bool:
        if seq <= self._last_sent:
            return False
        self._last_sent = seq
        return True

    @property
    def last_sent(self) -> int:
        return self._last_sent
