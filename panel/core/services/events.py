"""Persistencia y publicación de eventos (§4.1). Postgres primero, Redis
después. El `seq` lo asigna el worker (contador en memoria inicializado con
MAX(seq)+1 al arrancar), no la DB — un autoincrement global rompería el orden
por sesión (§6).

FASE B (DUAL_WRITE): si llega `ui_event` (UIEvent v1 normalizado), se
persiste en `Event.ui_event`. Si llega None, queda null — el front sabe
caer al `payload` crudo (backfill amigable, nunca rompe la UI)."""

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


def persist_event(
    session: Session,
    seq: int,
    type_: str,
    payload: dict,
    *,
    ui_event: dict | None = None,
) -> Event | None:
    """Guarda el evento (crudo + opcional UIEvent). Devuelve None si ese
    (session, seq) ya existía (idempotente ante reintentos tras un crash
    entre persistir y publicar)."""
    try:
        # Savepoint propio: si (session, seq) ya existía, el IntegrityError no
        # envenena la transacción externa.
        with transaction.atomic():
            return Event.objects.create(
                session=session,
                seq=seq,
                type=type_,
                payload=payload,
                ui_event=ui_event,
                ts=datetime.now(UTC),
            )
    except IntegrityError:
        return None


def publish_event(redis, sid: str, event: Event) -> None:
    """Publica en el pubsub `out` el mismo evento que se persistió.
    Incluye `ui_event` cuando está presente (FASE B)."""
    redis.publish(
        bus.key_out(sid),
        json.dumps(
            {
                "seq": event.seq,
                "type": event.type,
                "payload": event.payload,
                "ui_event": event.ui_event,
                "ts": event.ts.isoformat(),
            }
        ),
    )
