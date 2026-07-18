"""Persistencia y publicación de eventos (§4.1). Postgres primero, Redis
después. El `seq` lo asigna el worker (contador en memoria inicializado con
MAX(seq)+1 al arrancar), no la DB — un autoincrement global rompería el orden
por sesión (§6)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from django.db import IntegrityError, transaction

from panel.core import bus
from panel.core.models import Event, Session


def initial_seq(session: Session) -> int:
    """Siguiente seq a usar al arrancar el worker: MAX(seq)+1 de la sesión."""
    last = session.events.order_by("-seq").values_list("seq", flat=True).first()
    return (last + 1) if last is not None else 1


def persist_event(session: Session, seq: int, type_: str, payload: dict) -> Event | None:
    """Guarda el evento. Devuelve None si ese (session, seq) ya existía
    (idempotente ante reintentos tras un crash entre persistir y publicar)."""
    try:
        # Savepoint propio: si (session, seq) ya existía, el IntegrityError no
        # envenena la transacción externa.
        with transaction.atomic():
            return Event.objects.create(
                session=session,
                seq=seq,
                type=type_,
                payload=payload,
                ts=datetime.now(UTC),
            )
    except IntegrityError:
        return None


def publish_event(redis, sid: str, event: Event) -> None:
    """Publica en el pubsub `out` el mismo evento que se persistió."""
    redis.publish(
        bus.key_out(sid),
        json.dumps(
            {
                "seq": event.seq,
                "type": event.type,
                "payload": event.payload,
                "ts": event.ts.isoformat(),
            }
        ),
    )
