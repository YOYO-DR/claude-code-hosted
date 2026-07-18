"""Worker de sesión (§4.2). Un proceso asyncio por sesión, arrancado por
claude-session@<sid>.service (User=agents). Cola serial: un query() a la vez;
mensajes que llegan durante la ejecución se encolan en Redis.

Regla de persistencia: Postgres primero (con seq), Redis después. El seq lo
asigna el worker (contador en memoria = MAX(seq)+1 al arrancar)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "panel.settings")
django.setup()

import redis.asyncio as aioredis  # noqa: E402
from asgiref.sync import sync_to_async  # noqa: E402
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, PermissionMode  # noqa: E402
from django.conf import settings  # noqa: E402
from django.utils import timezone  # noqa: E402

from panel.core import bus  # noqa: E402
from panel.core.models import Event, Session  # noqa: E402
from panel.core.services import events as event_svc  # noqa: E402
from panel.core.services import serialize  # noqa: E402
from panel.core.services.model_env import render_env  # noqa: E402

log = logging.getLogger("session_worker")
HEARTBEAT_INTERVAL = 5  # segundos


class Worker:
    def __init__(self, sid: str) -> None:
        self.sid = sid
        self.redis = aioredis.from_url(settings.REDIS_URL)
        self._seq = 0
        self._stop = asyncio.Event()

    async def run(self) -> None:
        session = await self._load_session()
        self._seq = await sync_to_async(event_svc.initial_seq)(session)
        options = await self._build_options(session)

        await self._set_status(session, Session.Status.RUNNING, started=True)
        hb = asyncio.create_task(self._heartbeat())
        try:
            async with ClaudeSDKClient(options=options) as client:
                await self._loop(session, client)
        finally:
            hb.cancel()
            await self._set_status(session, Session.Status.STOPPED, ended=True)
            await self.redis.aclose()

    async def _loop(self, session: Session, client: ClaudeSDKClient) -> None:
        while not self._stop.is_set():
            popped = await self.redis.brpop([bus.key_in(self.sid)], timeout=5)
            if popped is None:
                continue
            _, raw = popped
            try:
                msg = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                log.warning("payload malformado en :in, descartado")
                continue

            mtype = msg.get("type")
            if mtype == "shutdown":
                self._stop.set()
                break
            if mtype == "interrupt":
                await client.interrupt()
                continue
            if mtype == "user_message":
                await self._run_turn(session, client, msg.get("text", ""))
            else:
                log.warning("tipo de mensaje desconocido en :in: %r", mtype)

    async def _run_turn(self, session: Session, client: ClaudeSDKClient, text: str) -> None:
        await self._set_status(session, Session.Status.RUNNING)
        await client.query(text)
        async for sdk_msg in client.receive_response():
            await self._emit(session, sdk_msg)
        await self._set_status(session, Session.Status.IDLE)

    async def _emit(self, session: Session, sdk_msg: object) -> None:
        etype = serialize.event_type(sdk_msg)
        payload = serialize.serialize_message(sdk_msg)
        # Postgres primero (con seq); si ya existía, no re-publicar.
        event = await sync_to_async(event_svc.persist_event)(session, self._seq, etype, payload)
        if event is None:
            return
        self._seq += 1
        await self._update_session_from_message(session, etype, payload)
        # Redis después (best-effort: si está caído, el evento ya está en PG).
        try:
            await sync_to_async(self._publish)(event)
        except Exception as exc:  # noqa: BLE001
            log.warning("publish falló (evento ya en PG): %s", exc)

    def _publish(self, event: Event) -> None:
        import redis as sync_redis

        r = sync_redis.from_url(settings.REDIS_URL)
        try:
            event_svc.publish_event(r, self.sid, event)
        finally:
            r.close()

    async def _update_session_from_message(
        self, session: Session, etype: str, payload: dict
    ) -> None:
        fields: dict[str, object] = {}
        if etype == "system.init":
            data = payload.get("data") or {}
            if data.get("session_id"):
                fields["sdk_session_id"] = data["session_id"]
            if data.get("model"):
                fields["model_reported"] = data["model"]
        elif etype == "result":
            if payload.get("total_cost_usd") is not None:
                fields["total_cost_usd"] = payload["total_cost_usd"]
            if payload.get("session_id"):
                fields["sdk_session_id"] = payload["session_id"]
        if fields:
            for k, v in fields.items():
                setattr(session, k, v)
            await sync_to_async(session.save)(update_fields=[*fields.keys(), "updated_at"])

    async def _heartbeat(self) -> None:
        while True:
            try:
                await self.redis.set(
                    bus.key_heartbeat(self.sid), timezone.now().isoformat(), ex=bus.HEARTBEAT_TTL
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("heartbeat falló: %s", exc)
            await asyncio.sleep(HEARTBEAT_INTERVAL)

    async def _build_options(self, session: Session) -> ClaudeAgentOptions:
        project = await sync_to_async(lambda: session.project)()
        policy = await sync_to_async(lambda: project.permission_policy)()
        profile = await sync_to_async(lambda: project.model_profile)()
        env = await sync_to_async(render_env)(profile)
        mode: PermissionMode = "bypassPermissions" if policy.mode == "auto" else "default"
        return ClaudeAgentOptions(
            cwd=project.path,
            permission_mode=mode,
            allowed_tools=list(policy.allowed_tools or []),
            model=profile.model or None,
            env=env,
            setting_sources=["user", "project"],
        )

    @sync_to_async
    def _load_session(self) -> Session:
        return Session.objects.select_related(
            "project", "project__permission_policy", "project__model_profile"
        ).get(id=self.sid)

    @sync_to_async
    def _set_status(
        self, session: Session, status: str, *, started: bool = False, ended: bool = False
    ) -> None:
        session.status = status
        fields = ["status", "updated_at"]
        if started and session.started_at is None:
            session.started_at = timezone.now()
            fields.append("started_at")
        if ended:
            session.ended_at = timezone.now()
            fields.append("ended_at")
        session.save(update_fields=fields)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sid = os.environ.get("SESSION_ID")
    if not sid:
        raise SystemExit("SESSION_ID no está en el entorno")
    worker = Worker(sid)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _on_term(*_: object) -> None:
        worker._stop.set()

    for s in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(s, _on_term)

    loop.run_until_complete(worker.run())


if __name__ == "__main__":
    main()
