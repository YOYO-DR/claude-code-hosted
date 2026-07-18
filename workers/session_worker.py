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
from claude_agent_sdk import (  # noqa: E402
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionMode,
    PermissionResultAllow,
    PermissionResultDeny,
)
from django.conf import settings  # noqa: E402
from django.utils import timezone  # noqa: E402

from panel.core import bus  # noqa: E402
from panel.core.models import Event, Session  # noqa: E402
from panel.core.services import events as event_svc  # noqa: E402
from panel.core.services import permissions as perm_svc  # noqa: E402
from panel.core.services import serialize  # noqa: E402
from panel.core.services.model_env import render_env  # noqa: E402

log = logging.getLogger("session_worker")
HEARTBEAT_INTERVAL = 5  # segundos

DENY_MSG = (
    "Permiso denegado por el operador. No reintentes esta acción; continúa con "
    "lo que no la requiera o documenta el bloqueo en NOTES.md."
)
TIMEOUT_MSG = (
    "La solicitud de aprobación expiró sin respuesta. Continúa con lo que no "
    "requiera este permiso o deja el trabajo limpio y documentado en NOTES.md."
)


def _allow_suggestions(ctx: object) -> list:
    """PermissionUpdate crudos de tipo addRules/allow que sugiere el SDK para este
    tool use. Se devuelven al SDK como `updated_permissions` (efecto live en la
    sesión) y se derivan a strings para persistir en la policy."""
    return [
        upd
        for upd in (getattr(ctx, "suggestions", None) or [])
        if getattr(upd, "type", None) == "addRules" and getattr(upd, "behavior", None) == "allow"
    ]


def _rules_to_strings(updates: list) -> list[str]:
    """Convierte PermissionUpdate a entradas de settings.json `ToolName(content)`
    (p.ej. `Bash(git push *)`)."""
    rules: list[str] = []
    for upd in updates:
        for r in getattr(upd, "rules", None) or []:
            tool = getattr(r, "tool_name", None)
            if not tool:
                continue
            content = getattr(r, "rule_content", None)
            rules.append(f"{tool}({content})" if content else tool)
    return rules


def _suggested_allow_rules(ctx: object) -> list[str]:
    """Atajo: strings de reglas 'allow' sugeridas (usado en tests)."""
    return _rules_to_strings(_allow_suggestions(ctx))


def redis_exceptions() -> tuple[type[BaseException], ...]:
    """Tipos de error que redis-py puede lanzar cuando el bus está caído o
    inestable. Import lazy para evitar cargar redis en frío."""
    import redis.exceptions as r

    return (
        r.ConnectionError,
        r.TimeoutError,
        r.BusyLoadingError,
        OSError,  # ConnectionResetError, BrokenPipeError, etc.
    )


class Worker:
    def __init__(self, sid: str) -> None:
        self.sid = sid
        self.redis = aioredis.from_url(settings.REDIS_URL)
        self._seq = 0
        self._session: Session | None = None
        self._stop = asyncio.Event()

    async def run(self) -> None:
        session = await self._load_session()
        self._session = session
        # Requests pendientes de un worker anterior jamás deben quedar aprobables.
        await sync_to_async(perm_svc.expire_pending)(session)
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
            try:
                popped = await self.redis.brpop([bus.key_in(self.sid)], timeout=5)
            except redis_exceptions() as exc:
                # Redis caído o inestable: log y reintentar. El loop NO muere;
                # cuando vuelva el bus, se sigue leyendo la cola (PG ya tiene los
                # eventos persistidos de cualquier turno en curso, §4.1).
                log.warning("bus Redis no disponible (%s); reintentando", exc)
                await asyncio.sleep(1.0)
                continue
            if popped is None:
                # brpop devolvio None solo si expiro el timeout, no por error.
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
        approve = policy.mode == "approve"
        mode: PermissionMode = "default" if approve else "bypassPermissions"
        return ClaudeAgentOptions(
            cwd=project.path,
            permission_mode=mode,
            allowed_tools=list(policy.allowed_tools or []),
            # El callback solo se consulta en zona indecisa (ni allow ni deny
            # obligatoria). En modo auto (bypass) el SDK ni lo llama.
            can_use_tool=self._can_use_tool if approve else None,
            model=profile.model or None,
            env=env,
            setting_sources=["user", "project"],
        )

    async def _can_use_tool(
        self, tool_name: str, input_data: dict, ctx: object
    ) -> PermissionResultAllow | PermissionResultDeny:
        """§4.2: crea PermissionRequest, publica en :perm, espera la respuesta y
        resuelve. La reescritura (si aplica) va en updated_input."""
        session = self._session
        assert session is not None  # seteado en run() antes de cualquier turno
        suggestions = _allow_suggestions(ctx)
        rule_strings = _rules_to_strings(suggestions)
        await self._set_status(session, Session.Status.WAITING_APPROVAL)
        try:
            answer, effective, changed, _req = await perm_svc.request_and_wait(
                session, tool_name, input_data, aredis=self.redis, always_rules=rule_strings
            )
        finally:
            await self._set_status(session, Session.Status.RUNNING)
        if answer in ("allow", "allow_always"):
            kwargs: dict = {}
            if changed:
                kwargs["updated_input"] = effective
            # allow_always: aplica las reglas sugeridas EN VIVO (mismo turno/sesión)
            # para que la próxima invocación que case no vuelva a preguntar.
            if answer == "allow_always" and suggestions:
                kwargs["updated_permissions"] = suggestions
            return PermissionResultAllow(**kwargs)
        return PermissionResultDeny(message=DENY_MSG if answer == "deny" else TIMEOUT_MSG)

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
