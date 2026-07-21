"""WebSocket de sesión (§4.4). Backlog desde Postgres, luego live desde el
pubsub de Redis, deduplicando por seq. Auth por sesión Django."""

from __future__ import annotations

import asyncio
import json
import uuid as _uuid

import redis.asyncio as aioredis
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings

from panel.core import bus
from panel.core.stream import SeqDedup


def _perm_seq(perm_id: str) -> int:
    """SP7: seq sintético estable por PermissionRequest. El canal `perm` es
    independiente del stream de eventos (que va por `out`), así que sus
    mensajes no tienen seq real. Para que el cliente los dedupe como el resto
    del backlog, derivamos un int firmado del uuid: dos perm requests del
    mismo session siempre tienen uuids distintos y, por tanto, seqs distintos."""
    return int.from_bytes(_uuid.UUID(perm_id).bytes[:4], "big", signed=True)


def make_redis():
    """Cliente Redis async del consumer. Función aparte para que los tests
    puedan inyectar un fake. `socket_timeout=None`: evita el default de 5s de
    redis-py async (pisa los `brpop(timeout=N)` y los pubsub.listen)."""
    return aioredis.from_url(settings.REDIS_URL, socket_timeout=None)


class SessionConsumer(AsyncWebsocketConsumer):
    async def connect(self) -> None:
        # Aceptar primero: un close() antes de accept() se traduce a un rechazo
        # HTTP 403 y el código 4401 se pierde (el navegador vería 1006). Para
        # entregar el 4401 observable hay que aceptar y luego cerrar.
        await self.accept()

        user = self.scope.get("user")
        if user is None or not user.is_authenticated:
            await self.close(code=4401)
            return

        self.sid = self.scope["url_route"]["kwargs"]["sid"]
        if not await self._session_exists(self.sid):
            await self.close(code=4404)
            return

        try:
            last_seq = int(self._query_param("last_seq", "0"))
        except (TypeError, ValueError):
            last_seq = 0

        self._dedup = SeqDedup(last_seq)
        self._redis = make_redis()
        self._pubsub = self._redis.pubsub()
        await self._pubsub.subscribe(bus.key_out(self.sid), bus.key_perm(self.sid))
        # Suscribirse ANTES de leer el backlog evita perder eventos que lleguen
        # en medio; los solapados los filtra SeqDedup por seq.
        self._reader = asyncio.create_task(self._read_pubsub())
        await self._send_backlog(last_seq)

    async def disconnect(self, code: int) -> None:
        reader = getattr(self, "_reader", None)
        if reader is not None:
            reader.cancel()
        pubsub = getattr(self, "_pubsub", None)
        if pubsub is not None:
            await pubsub.aclose()
        redis = getattr(self, "_redis", None)
        if redis is not None:
            await redis.aclose()

    async def receive(self, text_data: str | None = None, bytes_data: bytes | None = None) -> None:
        if not text_data:
            return
        try:
            msg = json.loads(text_data)
        except json.JSONDecodeError:
            return
        mtype = msg.get("type")
        if mtype == "user_message":
            text = msg.get("text", "")
            await self._redis.lpush(
                bus.key_in(self.sid), json.dumps({"type": "user_message", "text": text})
            )
        elif mtype == "interrupt":
            await self._redis.lpush(bus.key_in(self.sid), json.dumps({"type": "interrupt"}))
        elif mtype == "approve":
            # Fase 3: resolución de permisos vía web.
            request_id = msg.get("request_id")
            answer = msg.get("answer")
            if request_id and answer in {"allow", "deny", "allow_always"}:
                # Formato answer|source (source=web desde el WS del panel).
                await self._redis.set(
                    bus.key_answer(request_id), f"{answer}|web", nx=True, ex=bus.ANSWER_TTL
                )

    # -- internos --------------------------------------------------------

    async def _send_backlog(self, last_seq: int) -> None:
        for ev in await self._fetch_backlog(self.sid, last_seq):
            if self._dedup.should_forward(ev["seq"]):
                # FASE C: el SPA espera {seq, type, payload, ui_event, ts}
                # plano (ver panel/ui/spa/src/lib/ws.ts::RawEventMessage).
                # Antes envolvíamos en {channel, event} y el cliente no
                # encontraba seq/type/payload. Mantenemos compatibilidad
                # publicando AMBOS formatos — el cliente ignora lo que no
                # necesita; el campo extra `event` es inofensivo.
                await self.send(text_data=json.dumps(
                    {**ev, "_channel": "out", "_event": ev}
                ))

    async def _read_pubsub(self) -> None:
        async for message in self._pubsub.listen():
            if message.get("type") != "message":
                continue
            channel = message["channel"]
            if isinstance(channel, bytes):
                channel = channel.decode()
            try:
                data = json.loads(message["data"])
            except (json.JSONDecodeError, TypeError):
                continue
            if channel == bus.key_out(self.sid):
                seq = data.get("seq")
                if seq is not None and not self._dedup.should_forward(seq):
                    continue
                # Mismo shape plano que _send_backlog (FASE C).
                await self.send(text_data=json.dumps(
                    {**data, "_channel": "out", "_event": data}
                ))
            elif channel == bus.key_perm(self.sid):
                # SP7: el chat espera mensajes con shape {seq, type, payload,
                # ui_event, ts} para renderizar bubbles. El `perm` channel emite
                # `serialize_request` (dict plano sin kind/ui_event), así que
                # antes el cliente descartaba el mensaje en silencio (ver
                # ingestEvent en SessionDetail.tsx: `if (!msg.ui_event) return`)
                # y el usuario solo veía el `tool_call` con tag amarillo sin
                # poder aprobar. Lo envolvemos como UIEvent v1 permission_request
                # con seq sintético estable por uuid para dedupe.
                perm_id = str(data.get("id") or "")
                if not perm_id:
                    continue
                ts = data.get("expires_at") or data.get("created_at") or ""
                payload = {
                    "id": perm_id,
                    "tool": data.get("tool", ""),
                    "input_preview": data.get("input_preview", ""),
                }
                msg = {
                    "seq": _perm_seq(perm_id),
                    "type": "permission_request",
                    "payload": data,
                    "ui_event": {
                        "v": 1,
                        "seq": _perm_seq(perm_id),
                        "session_id": self.sid,
                        "ts": ts,
                        "kind": "permission_request",
                        "payload": payload,
                    },
                    "ts": ts,
                    "_channel": "perm",
                    "id": perm_id,
                }
                await self.send(text_data=json.dumps(msg))

    def _query_param(self, name: str, default: str) -> str:
        qs = self.scope.get("query_string", b"").decode()
        for pair in qs.split("&"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                if k == name:
                    return v
        return default

    @database_sync_to_async
    def _session_exists(self, sid: str) -> bool:
        from panel.core.models import Session

        return Session.objects.filter(id=sid).exists()

    @database_sync_to_async
    def _fetch_backlog(self, sid: str, last_seq: int) -> list[dict]:
        from panel.core.models import Event

        rows = Event.objects.filter(session_id=sid, seq__gt=last_seq).order_by("seq")
        return [
            {"seq": r.seq, "type": r.type, "payload": r.payload, "ts": r.ts.isoformat()}
            for r in rows
        ]
