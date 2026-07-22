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
from dataclasses import replace

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

from mcp_github import server as github_mcp  # noqa: E402
from mcp_ports import server as ports_mcp  # noqa: E402
from panel.core import bus  # noqa: E402
from panel.core.models import Event, Project, Session  # noqa: E402
from panel.core.services import events as event_svc  # noqa: E402
from panel.core.services import github as gh_svc  # noqa: E402
from panel.core.services import permissions as perm_svc  # noqa: E402
from panel.core.services import ports as ports_svc  # noqa: E402
from panel.core.services import serialize  # noqa: E402
from panel.core.services.model_env import render_env  # noqa: E402

log = logging.getLogger("session_worker")
HEARTBEAT_INTERVAL = 5  # segundos
CONTEXT_USAGE_INTERVAL = 10  # segundos (más caro que heartbeat, va al SDK)
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


def _extract_commands(info: dict | None) -> list[dict]:
    """SP12: normaliza la lista de comandos del init del CLI (get_server_info)
    a [{name, description}]. El shape exacto de cada item lo decide el CLI, así
    que leemos defensivamente (dict con name/command + description/summary, o
    string suelto)."""
    if not info:
        return []
    raw = info.get("commands") or info.get("slash_commands") or []
    out: list[dict] = []
    for c in raw:
        if isinstance(c, dict):
            name = c.get("name") or c.get("command") or ""
            desc = c.get("description") or c.get("summary") or ""
        else:
            name, desc = str(c), ""
        name = str(name).lstrip("/")
        if name:
            out.append({"name": name, "description": str(desc)})
    return out


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
        # socket_timeout=None: redis-py async usa DEFAULT_SOCKET_TIMEOUT=5s, que
        # coincide con el `brpop(timeout=5)` y dispara TimeoutError ANTES de que
        # el servidor responda nil. Con None el socket espera lo que haga falta.
        self.redis = aioredis.from_url(settings.REDIS_URL, socket_timeout=None)
        self._seq = 0
        self._session: Session | None = None
        self._slug: str = ""
        self._stop = asyncio.Event()
        # FASE B: acumulador de deltas de stream_event por content_block_index.
        from panel.core.events.normalize import StreamAccumulator
        self._stream_acc = StreamAccumulator()
        # FASE C.6: cache del último git_branch emitido (branch+'|'+dirty).
        self._last_git_state: str | None = None
        # SP12: contexto por modelo (poblados en _build_options).
        self._max_context_tokens: int | None = None
        self._auto_compact_threshold: int | None = None
        # SP12: la lista de comandos `/` se emite una sola vez (viene del init).
        self._slash_emitted = False
        # SP12: loguear la respuesta cruda de get_context_usage una sola vez
        # (verificar qué reporta el CLI para max/umbral de auto-compact).
        self._ctx_logged = False

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
            await self._run_client(session, options)
        finally:
            hb.cancel()
            await self._set_status(session, Session.Status.STOPPED, ended=True)
            await self.redis.aclose()

    async def _run_client(self, session: Session, options: ClaudeAgentOptions) -> None:
        """Abre el cliente del SDK y corre el loop.

        SP6: si el arranque falla CON `resume` (transcript borrado a mano, id
        que el CLI ya no conoce), reintenta UNA vez sin contexto en vez de
        morir y dejar a systemd en bucle de reinicio. `connected` distingue
        el fallo de arranque de un fallo a mitad de conversación: ese último
        se propaga tal cual, nunca se reintenta el turno.
        """
        connected = False
        try:
            async with ClaudeSDKClient(options=options) as client:
                connected = True
                self._client = client
                ctx_task = asyncio.create_task(self._poll_context_usage(session))
                try:
                    await self._loop(session, client)
                finally:
                    ctx_task.cancel()
                    self._client = None
        except Exception:
            if connected or not options.resume:
                raise
            log.exception(
                "resume de %s falló; reintento sin contexto previo", options.resume
            )
            await sync_to_async(self._clear_sdk_session)(session)
            async with ClaudeSDKClient(options=replace(options, resume=None)) as client:
                self._client = client
                ctx_task = asyncio.create_task(self._poll_context_usage(session))
                try:
                    await self._loop(session, client)
                finally:
                    ctx_task.cancel()
                    self._client = None

    def _clear_sdk_session(self, session: Session) -> None:
        """Olvida el id del SDK que no se pudo reanudar para que el próximo
        arranque no vuelva a intentarlo."""
        session.sdk_session_id = None
        session.save(update_fields=["sdk_session_id", "updated_at"])

    async def _poll_context_usage(self, session: Session) -> None:
        """SP11: emite UIEvent `context_usage {totalTokens, maxTokens,
        percentage, model}` cada CONTEXT_USAGE_INTERVAL. Best-effort: si
        `get_context_usage()` falla (CLI no soporta aún el flag, race con
        cierre del SDK, etc.), se loguea una vez y se sale silencioso. La
        SPA consume el último válido sin reintentar.
        """
        from panel.core.events.normalize import UIEvent
        warned = False
        # SP12: emitir una vez la lista de comandos `/` (built-in + custom +
        # plugins) que reporta el init del CLI, para el menú del chat.
        await self._maybe_emit_slash_commands(session)
        while not self._stop.is_set():
            resp = None
            try:
                if self._client is not None:
                    # get_context_usage es ASYNC (devuelve coroutine),
                    # contrario a lo que el nombre sugiere — descubrir
                    # este bug costó un round-trip al log del worker.
                    resp = await self._client.get_context_usage()
            except Exception as exc:  # noqa: BLE001
                if not warned:
                    log.warning("context_usage poll falló (se silencia): %s", exc)
                    warned = True
            if resp is not None and not self._ctx_logged:
                self._ctx_logged = True
                log.info(
                    "context_usage RAW: maxTokens=%s rawMaxTokens=%s percentage=%s "
                    "autoCompactThreshold=%s isAutoCompactEnabled=%s",
                    resp.get("maxTokens"), resp.get("rawMaxTokens"),
                    resp.get("percentage"), resp.get("autoCompactThreshold"),
                    resp.get("isAutoCompactEnabled"),
                )
            if resp is not None:
                try:
                    total = int(resp.get("totalTokens", 0))
                    max_tokens = int(resp.get("maxTokens", 0) or 0)
                    # SP12: override del denominador si el modelo lo define
                    # (proveedores custom reportan un max incorrecto).
                    if self._max_context_tokens:
                        max_tokens = int(self._max_context_tokens)
                    pct = (
                        (total / max_tokens * 100.0)
                        if max_tokens > 0
                        else float(resp.get("percentage", 0.0))
                    )
                    # Umbral de auto-compact como %: prioridad al valor del
                    # modelo; si no, derivar del que reporta el SDK (en tokens).
                    threshold = self._auto_compact_threshold
                    if threshold is None:
                        act = resp.get("autoCompactThreshold")
                        if act and max_tokens > 0:
                            threshold = round(float(act) / max_tokens * 100.0)
                    payload = {
                        "total_tokens": total,
                        "max_tokens": max_tokens,
                        "percentage": pct,
                        "model": str(resp.get("model", "")),
                        "auto_compact_threshold": threshold,
                        "auto_compact_enabled": bool(resp.get("isAutoCompactEnabled", False)),
                    }
                    ue = UIEvent(
                        v=1, seq=0, session_id=str(session.id),
                        ts="", kind="context_usage",
                        payload=payload,
                    )
                    self._redis_publish_ui(ue.to_dict())
                except Exception as exc:  # noqa: BLE001
                    if not warned:
                        log.warning("context_usage publish falló (se silencia): %s", exc)
                        warned = True
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=CONTEXT_USAGE_INTERVAL)
            except asyncio.TimeoutError:
                pass

    async def _maybe_emit_slash_commands(self, session: Session) -> None:
        """SP12: publica UNA vez el UIEvent efímero `slash_commands` con los
        comandos disponibles (get_server_info del init del CLI). Best-effort."""
        if self._slash_emitted or self._client is None:
            return
        try:
            info = await self._client.get_server_info()
        except Exception as exc:  # noqa: BLE001
            log.warning("get_server_info falló (se silencia): %s", exc)
            return
        commands = _extract_commands(info)
        if not commands:
            return
        from panel.core.events.normalize import UIEvent
        ue = UIEvent(
            v=1, seq=0, session_id=str(session.id), ts="",
            kind="slash_commands", payload={"commands": commands},
        )
        try:
            self._redis_publish_ui(ue.to_dict())
            self._slash_emitted = True
        except Exception as exc:  # noqa: BLE001
            log.warning("slash_commands publish falló (se silencia): %s", exc)

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
        # Nuevo turno: olvida si hubo streaming (anti-duplicado en _emit).
        self._stream_acc.reset_turn()
        await client.query(text)
        async for sdk_msg in client.receive_response():
            await self._emit(session, sdk_msg)
        await self._set_status(session, Session.Status.IDLE)

    async def _emit(self, session: Session, sdk_msg: object) -> None:
        etype = serialize.event_type(sdk_msg)
        payload = serialize.serialize_message(sdk_msg)
        # FASE B: normalizar a UIEvent v1 (DUAL_WRITE).
        # StreamEvent es especial: el normalizer puede emitir VARIOS UIEvent
        # (uno por delta de texto/thinking) — los persistimos con seq
        # compartido (los deltas comparten seq con el stream_event crudo)
        # para que el seq siga siendo único por (session,seq). Para evitar
        # duplicar el constraint UNIQUE(session,seq), los UIEvent de streaming
        # NO se persisten en BD: se publican SOLO por Redis (efecto en vivo).
        # El AssistantMessage "macro" final trae el texto completo y SÍ
        # persiste el UIEvent agent_text streaming=False en BD (snapshot).
        from claude_agent_sdk import StreamEvent as _StreamEvent  # noqa: I001
        from panel.core.events import normalize as norm

        if isinstance(sdk_msg, _StreamEvent):
            # 1) Persistimos el stream_event crudo como siempre.
            event = await sync_to_async(event_svc.persist_event)(
                session, self._seq, etype, payload
            )
            if event is None:
                return
            self._seq += 1
            # 2) Deltas → UIEvent efímeros por Redis (no BD).
            ui_events = self._stream_acc.feed_stream(sdk_msg.event or {})
            for ue in ui_events:
                ue.seq = event.seq  # mismo seq del stream_event crudo
                ue.session_id = str(session.id)
                try:
                    self._redis_publish_ui(ue.to_dict())
                except Exception as exc:  # noqa: BLE001
                    log.warning("publish UI delta falló: %s", exc)
            return

        # Mensaje macro (assistant/user/system/result): persistir crudo + TODOS
        # los UIEvent, cada uno con su PROPIO seq.
        # BUGFIX reload: antes solo se persistía ui_events[0]. Un
        # AssistantMessage con [thinking, text, tool_use] perdía text y
        # tool_call al recargar (solo sobrevivía el primer bloque, el thinking
        # con extended thinking). Ahora cada bloque va a su fila.
        ui_events = norm.normalize_sdk_message(
            sdk_msg, seq=self._seq, session_id=str(session.id),
        )
        if not ui_events:
            # Mensaje sin UIEvent (p.ej. system.thinking_tokens): persistir crudo
            # para auditoría, sin burbuja. No se publica.
            event = await sync_to_async(event_svc.persist_event)(
                session, self._seq, etype, payload, ui_event=None,
            )
            if event is not None:
                self._seq += 1
                await self._update_session_from_message(session, etype, payload)
            await self._maybe_emit_git_branch(session, sdk_msg)
            return

        # Anti-duplicado: si ya hubo deltas de streaming este turno, el
        # texto/thinking macro ya se vieron en vivo — se PERSISTEN (para reload)
        # pero NO se re-publican. Si el proveedor no hace streaming (deltas=0),
        # se publican para que aparezcan en vivo.
        streamed = self._stream_acc.produced_this_turn()
        await self._update_session_from_message(session, etype, payload)
        for i, ue in enumerate(ui_events):
            ue.seq = self._seq
            row_payload = payload if i == 0 else {}
            row_type = etype if i == 0 else ue.kind
            event = await sync_to_async(event_svc.persist_event)(
                session, self._seq, row_type, row_payload, ui_event=ue.to_dict(),
            )
            self._seq += 1
            if event is None:
                continue  # (session, seq) ya existía (replay idempotente)
            if ue.kind in ("agent_text", "agent_thinking") and streamed:
                continue  # ya visto por streaming; persistido para reload
            # Redis después (best-effort: si está caído, ya está en PG).
            try:
                await sync_to_async(self._publish)(event)
            except Exception as exc:  # noqa: BLE001
                log.warning("publish falló (evento ya en PG): %s", exc)
        # FASE C.6: si el evento puede haber mutado el repo, emite UIEvent
        # git_branch si la rama o el dirty cambió. Polling barato
        # (rev-parse + status --porcelain, <50ms típico).
        await self._maybe_emit_git_branch(session, sdk_msg)

    async def _maybe_emit_git_branch(self, session: Session, sdk_msg: object) -> None:
        """FASE C.6: tras eventos que PUEDEN mover el repo (Bash con git,
        Edit, Write), corre `git rev-parse --abbrev-ref HEAD + git status
        --porcelain` y, si rama o dirty cambiaron respecto al cache local,
        emite un UIEvent `git_branch {branch, dirty}` por Redis (no se
        persiste en BD: es estado derivado, se puede recomputar con el
        path del proyecto).

        El cache vive en `self._last_git_state` (rama+'|'+str(dirty)) para
        no emitir duplicados cuando el tool no cambió nada en el repo.
        Si git falla (no es repo, no instalado), salimos silenciosamente.
        """
        import asyncio as _asyncio

        # Filtra herramientas que mutan el repo.
        mutating = False
        try:
            from claude_agent_sdk import (
                AssistantMessage,
                ToolResultBlock,
                ToolUseBlock,
                UserMessage,
            )
            blocks = []
            if isinstance(sdk_msg, AssistantMessage):
                blocks = sdk_msg.content or []
            elif isinstance(sdk_msg, UserMessage):
                # UserMessage.content puede ser str | list; solo nos interesa list.
                content = sdk_msg.content
                blocks = content if isinstance(content, list) else []
            else:
                blocks = []
            for b in blocks:
                if isinstance(b, ToolUseBlock):
                    name = b.name or ""
                    if name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
                        mutating = True
                    elif name == "Bash":
                        # Heurística barata: si el comando menciona "git"
                        # (cualquier operación que mueva HEAD/index).
                        cmd = (b.input or {}).get("command", "") or ""
                        if "git " in cmd or cmd.startswith("git"):
                            mutating = True
                elif isinstance(b, ToolResultBlock):
                    # Resultado de tool → si el tool mutó, ya sabemos que mutating.
                    pass
        except Exception:
            return
        if not mutating:
            return

        proj = session.project
        path = proj.path
        try:
            proc = await _asyncio.create_subprocess_exec(
                "git", "-C", path, "rev-parse", "--abbrev-ref", "HEAD",
                stdout=_asyncio.subprocess.PIPE,
                stderr=_asyncio.subprocess.PIPE,
            )
            stdout, _ = await _asyncio.wait_for(proc.communicate(), timeout=2)
            if proc.returncode != 0:
                return
            branch = stdout.decode().strip() or "(unknown)"

            proc2 = await _asyncio.create_subprocess_exec(
                "git", "-C", path, "status", "--porcelain",
                stdout=_asyncio.subprocess.PIPE,
                stderr=_asyncio.subprocess.PIPE,
            )
            stdout2, _ = await _asyncio.wait_for(proc2.communicate(), timeout=2)
            dirty = bool(stdout2.decode().strip())
        except Exception:
            return

        state_key = f"{branch}|{dirty}"
        if state_key == self._last_git_state:
            return
        self._last_git_state = state_key

        # Emitir por Redis (no BD). El chat OpenHands muestra el cambio en vivo.
        from panel.core.events.normalize import UIEvent
        ue = UIEvent(
            v=1, seq=self._seq, session_id=str(session.id),
            ts="", kind="git_branch",
            payload={"branch": branch, "dirty": dirty},
        )
        self._redis_publish_ui(ue.to_dict())

    def _publish(self, event: Event) -> None:
        import redis as sync_redis

        r = sync_redis.from_url(settings.REDIS_URL)
        try:
            event_svc.publish_event(r, self.sid, event)
        finally:
            r.close()

    def _redis_publish_ui(self, ui_event: dict) -> None:
        """Publica un UIEvent efímero (típicamente un delta de streaming) en
        el canal `out` SIN persistirlo en BD. El cliente del chat lo recibe en
        vivo y lo descarta en cuanto llega el bloque 'macro' final.
        Usa el MISMO cliente síncrono que `_publish` para evitar un connection
        pool efímero por delta (alto throughput)."""
        import json  # noqa: I001
        import redis as sync_redis

        from panel.core import bus

        r = sync_redis.from_url(settings.REDIS_URL)
        try:
            r.publish(
                bus.key_out(self.sid),
                json.dumps(
                    {"seq": ui_event["seq"], "type": "ui_delta",
                     "payload": {}, "ui_event": ui_event,
                     "ts": ui_event["ts"]},
                ),
            )
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
        self._slug = project.slug
        # SP12: contexto por modelo para la barra + marcador de auto-compact.
        self._max_context_tokens = profile.max_context_tokens
        self._auto_compact_threshold = profile.auto_compact_threshold
        # SP12 (4b): `max_context_tokens` SÍ controla el CLI real vía
        # CLAUDE_CODE_MAX_CONTEXT_TOKENS (verificado en vivo: el SDK reporta el
        # nuevo maxTokens). setdefault: extra_env del modelo puede pisarlo.
        # El umbral de auto-compact NO es un % configurable: el CLI usa
        # threshold = maxTokens - 33000 (buffer fijo) y CLAUDE_AUTOCOMPACT_PCT_OVERRIDE
        # es un knob de test que no aplica en operación normal. Por eso
        # `auto_compact_threshold` es solo un marcador visual (advisory).
        if profile.max_context_tokens:
            env.setdefault("CLAUDE_CODE_MAX_CONTEXT_TOKENS", str(int(profile.max_context_tokens)))
        approve = policy.mode == "approve"
        mode: PermissionMode = "default" if approve else "bypassPermissions"
        # MCP de puertos in-process (§4.5): tokens/DB nunca a disco. Las tools
        # se auto-permiten (son la vía sancionada para obtener puertos).
        ports_server = ports_mcp.build_server(project.slug, self.sid)
        allowed = list(policy.allowed_tools or []) + ports_mcp.TOOL_NAMES
        mcp_servers: dict = {"ports": ports_server}
        # MCP de GitHub in-process (§5.3): solo si el proyecto lo tiene activo y
        # hay token. El token vive en memoria; el agente no puede mergear.
        gh_cfg = await sync_to_async(self._github_config)(project)
        if gh_cfg is not None:
            repo, token = gh_cfg
            mcp_servers["github"] = github_mcp.build_server(repo, project.path, token)
            allowed += github_mcp.TOOL_NAMES
        return ClaudeAgentOptions(
            cwd=project.path,
            permission_mode=mode,
            allowed_tools=allowed,
            mcp_servers=mcp_servers,
            # El callback solo se consulta en zona indecisa (ni allow ni deny
            # obligatoria). En modo auto (bypass) el SDK ni lo llama.
            can_use_tool=self._can_use_tool if approve else None,
            model=profile.model or None,
            env=env,
            setting_sources=["user", "project"],
            # SP6: `sdk_session_id` solo está poblado si la sesión ya tuvo un
            # turno — o sea, si esto es un restart (o una recuperación tras
            # crash). Reanudamos entonces la conversación del SDK para que el
            # agente conserve el contexto, igual que `claude --resume`. Una
            # sesión nueva lo tiene en None → arranque limpio. Cero estado
            # extra que mantener: el propio campo es la señal.
            resume=session.sdk_session_id or None,
            # FASE B: recibir stream_event con deltas token-a-token para que el
            # normalizador pueda emitir `agent_text.streaming=True` (efecto
            # "escribiendo…" del chat OpenHands). El AssistantMessage "macro"
            # sigue llegando al final con el texto completo.
            include_partial_messages=True,
        )

    async def _can_use_tool(
        self, tool_name: str, input_data: dict, ctx: object
    ) -> PermissionResultAllow | PermissionResultDeny:
        """§4.2: crea PermissionRequest, publica en :perm, espera la respuesta y
        resuelve. La reescritura (si aplica) va en updated_input."""
        session = self._session
        assert session is not None  # seteado en run() antes de cualquier turno
        # Hook de puertos (§4.2 task 4): binds a puertos de otro proyecto →
        # reescribe al propio o deniega. Solo Bash.
        if tool_name == "Bash" and self._slug:
            action, payload = await sync_to_async(ports_svc.guard_command)(
                self._slug, input_data.get("command", "")
            )
            if action == "deny":
                return PermissionResultDeny(message=payload or DENY_MSG)
            if action == "rewrite" and payload is not None:
                input_data = {**input_data, "command": payload}
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
            # allow_always: aplica las reglas sugeridas EN VIVO con destino
            # "session" para que persistan el resto de la sesión del SDK y la
            # próxima invocación que case no vuelva a preguntar.
            if answer == "allow_always" and suggestions:
                kwargs["updated_permissions"] = [
                    replace(u, destination="session") for u in suggestions
                ]
            return PermissionResultAllow(**kwargs)
        return PermissionResultDeny(message=DENY_MSG if answer == "deny" else TIMEOUT_MSG)

    def _github_config(self, project: Project) -> tuple[str, str] | None:
        """(repo, token) si el proyecto tiene GitHub activo y hay token; si no None."""
        if not (project.github_enabled and project.github_repo):
            return None
        token = gh_svc.get_token()
        if not token:
            return None
        return project.github_repo, token

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
