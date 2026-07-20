# DECISIONS.md

Desviaciones y resoluciones de ambigüedad respecto a PLAN.md, con su porqué.

## Fase 0

### D1 — Traefik en Docker con `network_mode: host`

PLAN.md fija Traefik en Docker y Postgres/Redis también en Docker, pero Django
(usuario `panel`) y los workers (usuario `agents`) corren nativos vía systemd
(necesitan invocar `systemctl`, git, npm, docker CLI, etc. directo sobre el
host — no tiene sentido meterlos en un contenedor). Eso implica que Traefik
(en Docker) tiene que enrutar hacia procesos nativos del host (ttyd, panel
Django). En Linux, `host.docker.internal` no existe por defecto sin
`extra_hosts: host-gateway`, así que la opción más simple y estándar es correr
el contenedor de Traefik con `network_mode: host`: bindea 80/443 directo en
el host y llega a `127.0.0.1:<puerto>` de cualquier servicio nativo sin
configuración extra.

**Por qué:** menos piezas móviles que `extra_hosts` + red bridge, y evita
duplicar el mapeo de puertos.

### D2 — "Postgres/Redis no expuestos fuera de la red Docker" = bind a 127.0.0.1, no a 0.0.0.0

Interpretación literal (contenedores sin ningún puerto publicado) es
incompatible con que Django/workers nativos necesiten conectarse a ellos.
Se publican puertos de Postgres/Redis únicamente en `127.0.0.1:<puerto>`
(nunca `0.0.0.0`), de forma que sean alcanzables solo desde el propio host
y nunca desde la red pública. El gate 0 (`ss -tlnp`) valida que no aparezcan
en `0.0.0.0` ni en la IP pública.

### D3 — Un solo dominio con ruteo por path, no subdominio por proyecto

Decisión explícita de Yoiner: en vez de `term-<slug>.<dom>` (subdominio por
proyecto, como dice PLAN.md §1), todo vive bajo un único host
`claude-code-hosted.yoyodr.dev` y el proyecto se distingue por path:

```
https://claude-code-hosted.yoyodr.dev/              → panel (UI + API)
https://claude-code-hosted.yoyodr.dev/projects/<slug>/terminal → ttyd del proyecto
https://claude-code-hosted.yoyodr.dev/tg/webhook     → webhook de Telegram (Fase 4, ya era path-based en el plan)
```

Esto además hace consistente el patrón: el webhook de Telegram ya era
path-based en PLAN.md §4.6; ahora ttyd sigue el mismo esquema en vez de ser
el único caso subdominio-based.

**Consecuencia en TLS:** con un solo hostname no hace falta wildcard ni
DNS-01/token de Cloudflare — un certificado HTTP-01 normal para
`claude-code-hosted.yoyodr.dev` alcanza para todo. Se descarta la pregunta
original de wildcard vs HTTP-01: ya no aplica.

**Consecuencia en ttyd:** cada instancia corre con `--base-path
/projects/<slug>/terminal` (soportado nativamente por ttyd) y Traefik enruta
por `PathPrefix` en vez de `Host`. El router se agrega/quita dinámicamente
(archivo de config dinámica que Traefik vigila) cuando se crea/archiva un
proyecto — encaja con el renderer de Fase 2, que ahora también materializa
esta pieza.

### D4 — Pool fijo de puertos ttyd (Fase 0, previo al MCP de puertos de Fase 4)

Fase 0 no tiene todavía ni `supervisor.py` (Fase 1) ni el `PortRegistry` /
MCP de puertos (Fase 4) — ese MCP es para puertos que los propios AGENTES
abren para sus servicios, no para infraestructura de la plataforma. Para
ttyd (interno, un puerto por slot) se reserva un rango fijo
`127.0.0.1:7681-7688` (8 slots, igual al límite de sesiones concurrentes del
plan) documentado en `INFRA.md`, con una asignación simple slug→puerto en
`/opt/panel/deploy/ttyd/ports.json` que el `ExecStartPre` de
`ttyd@.service` resuelve. Revisar si esto necesita algo más sofisticado en
Fase 2 cuando exista CRUD real de proyectos.

### D5 — TLS: Cloudflare Origin CA en vez de Let's Encrypt (dominio proxied)

El dominio `claude-code-hosted.yoyodr.dev` está **proxied por Cloudflare**
(nube naranja): `dig` devuelve IPs de Cloudflare (104.21.x / 172.67.x), no la
del VPS. Consecuencias:

- El browser ya recibe TLS válido del **edge de Cloudflare** (cert de Google
  Trust Services para `*.yoyodr.dev`). No hace falta emitir nada para el
  tramo browser→CF.
- El tramo **CF→origen** con Cloudflare en modo *Full (strict)* exige que el
  origen presente un cert que CF confíe. Con solo el `TRAEFIK DEFAULT CERT`
  autofirmado, CF devuelve **HTTP 526**.
- Let's Encrypt HTTP-01 **no aplica**: LE conecta contra las IPs de
  Cloudflare, no contra el origen, y CF intercepta 80/443.

Solución adoptada (decisión de Yoiner): **Cloudflare Origin CA certificate**.
Se genera vía API (`POST /certificates`, `request_type: origin-rsa`,
`requested_validity: 5475` = 15 años) usando un API token de Cloudflare con
permiso `Zone → SSL and Certificates → Edit` sobre la zona `yoyodr.dev`. La
private key se genera en el VPS (`/etc/panel/origin/key.pem`, nunca sale de
ahí); solo el CSR viaja a CF y vuelve firmado. El cert queda en
`/etc/panel/origin/cert.pem`.

Traefik lo sirve como **default certificate** vía file provider
(`deploy/traefik/dynamic/tls.yml`), montando `/etc/panel/origin` en el
contenedor. Se elimina toda la config ACME/Let's Encrypt del compose
(`--certificatesresolvers.le.*`, el volumen `traefik_certs`, el `env_file` de
`LE_EMAIL`) y `render_routes.py` usa `tls: {}` (cert default) en vez de
`certResolver: le`.

Cero renovaciones durante 15 años. Cloudflare queda en Full (strict).

Esto reemplaza por completo la pregunta original wildcard-vs-HTTP-01 (D3) y la
idea de `LE_EMAIL` en `install.sh`.

---

## D6 — Sintaxis de patrones de permisos del renderer (Fase 2)

`settings.json` usa el esquema oficial de Claude Code:
`{"permissions": {"allow": [...], "deny": [...]}}`. Los patrones siguen la
sintaxis `Tool(specifier)` con rutas estilo gitignore: `//abs/path/**` para
absolutas, `./rel` para relativas al cwd del proyecto, `**` recursivo. Las
`MANDATORY_DENY` (constante en código) ya usaban esta forma y se validó que
coincide con la doc; no hubo que ajustarlas.

El **modo** de permisos (auto→`bypassPermissions`, approve→`default`) NO va en
`settings.json`: lo fija el worker en `ClaudeAgentOptions.permission_mode`
(§4.2), coherente con "settings.json SIN env de modelo" (§4.3).

El **env del modelo** (tokens) nunca se materializa a disco: se inyecta desde
la DB en memoria del worker (§4.2/§4.3). El renderer solo escribe permisos,
skills y `.mcp.json`.

Badge "reinicio requerido": se computa comparando `updated_at` (auto_now) de
`McpServer`/`ModelProfile` del proyecto contra `session.started_at` — cero
campos ni migraciones nuevas.

---

## D7 — Modelo de privilegios del render/provisioning (Fase 2)

El panel corre como usuario `panel` (mínimo privilegio). Pero materializar
config y provisionar proyectos necesita root:
- **Leer `/etc/panel/panel.env`** (creds de DB) — 640 root:panel; el render lo
  necesita para conectar a Postgres. Sourcearlo requiere root.
- **`chown` del dir del proyecto a `agents`** — el worker (User=agents) escribe
  código ahí; `panel` no puede chownear a otro usuario.
- **Escribir config en dirs de `agents`** — root puede; `panel` no.

Solución (mismo patrón que `supervisor.py` con systemctl): dos helpers root
(`deploy/panel-render.sh`, `deploy/panel-provision.sh <slug> <path>`) invocados
por el panel vía `sudo -n`, con sudoers restringido (`sudoers.d-panel`). El
provision valida que el path esté bajo `/srv/projects` y el slug sea
`[a-z0-9-]`. `panel-provision.sh` hace mkdir + git init + chown agents + render.

`privileged.py` decide: root → render en proceso; `panel`+sudo+helper → sudo;
local/tests (sin helper) → en proceso sin chown. Los archivos de config quedan
root-owned pero world-readable (644): el agente los LEE, no los escribe. El dir
del proyecto queda `agents`-owned para que el worker escriba código.

Verificado en el VPS: deny duro de settings.json bloquea Read a
`/srv/projects/<otro>` y `~/.ssh` con
`<tool_use_error>File is in a directory that is denied by your permission
settings.</tool_use_error>`, incluso bajo `bypassPermissions` (deny > allow).

---

## D8 — Comportamiento del SDK para permisos (Fase 3)

Descubierto empíricamente contra el CLI bundled del `claude_agent_sdk`:

- **`can_use_tool` requiere streaming mode.** Con un prompt string levanta
  `ValueError`. El worker ya usa `ClaudeSDKClient` (conecta sin prompt y luego
  `query()`), que es streaming — OK. Al fijar `can_use_tool`, el SDK setea
  `permission_prompt_tool_name="stdio"` y enruta los permisos al callback.
- **El CLI auto-aprueba comandos Bash "seguros"** (p.ej. `echo`) sin consultar
  el callback. Solo acciones no triviales (`Write`, `git push`, `rm`, red…)
  pasan por `can_use_tool`. Los tests/e2e usan esas.
- **`permission_mode="default"` + allowlist vacía** ⇒ el callback se consulta
  para todo lo no-seguro y no denegado. La **deny obligatoria corta ANTES** del
  callback (verificado: Read a proyecto ajeno → `tool_use_error` sin invocar el
  callback).
- **allow_always live**: `PermissionResultAllow(updated_permissions=[...])` con
  `destination="session"` aplica la regla el resto de la sesión (la 2da
  invocación no pregunta). Las reglas scopeadas vienen de `ctx.suggestions`
  (`{tool_name, rule_content}` → `Bash(git push *)`), y **solo existen para
  comandos estándalone**, no compuestos (`cd && git push` no sugiere regla).
- **Persistencia entre sesiones**: la regla se guarda en
  `PermissionPolicy.allowed_tools` (DB, fuente de verdad); el worker la pasa por
  `ClaudeAgentOptions.allowed_tools` en la próxima sesión. El re-render de
  settings.json es best-effort: el worker corre como `agents` y no puede sudo el
  helper de render (sudoers es solo para `panel`); no afecta la correctitud.

Anomalía preexistente (no bloqueante): el `brpop` async del worker loguea
`Timeout reading from 127.0.0.1:6379` en los polls idle; los mensajes con datos
vuelven rápido, así que la entrega no se ve afectada. Pendiente de afinar el
socket/health-check del cliente Redis.

---

## D9 — MCP de puertos in-process, no stdio (Fase 4)

§4.5 sugiere montar `mcp_ports` como servidor **stdio** en `.mcp.json` con
`MCP_PROJECT_SLUG` inyectado por el renderer. Problema: un servidor stdio
necesitaría las **creds de Postgres en el env del .mcp.json**, que vive en el
directorio del proyecto y es **legible por el agente** (Read no lo deniega) →
fuga de la DB, viola "sin secretos a disco" (§4.3/§6).

Decisión: montar el MCP de puertos **in-process** con
`create_sdk_mcp_server(...)` pasado por `ClaudeAgentOptions.mcp_servers` desde el
worker. El worker ya tiene Django+DB en memoria; el slug es de confianza (lo fija
el worker, no el agente). Cero secretos a disco.

Trade-off: cubre las **sesiones-worker** (las que orquesta el panel, que son las
del gate E2E). La **escotilla ttyd** (uso manual del operador) NO obtiene el MCP
de puertos. Si en el futuro se quisiera, la vía correcta sería un servidor stdio
que llame a un endpoint localhost del panel (sin creds a disco), no inyectar la
DB en el .mcp.json.

El **hook de coordinación de puertos** vive en `can_use_tool` (§4.2 task 4) →
solo se consulta en modo `approve`. En `auto` (bypassPermissions) el SDK no llama
al callback, así que la coordinación depende de `allocate_port` + el prompt
global. Es coherente con "auto = confío en este proyecto".

---

## D10 — GitHub: MCP in-process sin merge + token efímero (Fase 5)

Decisiones de Yoiner: (a) sin `administration:rw` (él crea los repos y da acceso
al token); (b) **mismo PAT** para plataforma y agentes, guardado **cifrado en la
BD** (Config), no `gh login` en el VPS; (c) el token se pide **por frontend** y
el panel lo valida (autentica + lista repos).

Implementación:
- **MCP de GitHub in-process** (coherente con D9): `create_sdk_mcp_server` montado
  por el worker con el token en memoria (desde Config, descifrado). Ligado al
  `github_repo` del proyecto — el agente no puede apuntar a otro repo. Expone
  `open_pull_request` / `push_branch` / `list_pull_requests` / `comment_pull_request`.
- **"Sin merge" = no exponer tool de merge.** Como el token es el mismo (puede
  mergear por API), el enforcement fuerte es **branch protection** del repo; a
  nivel de agente, simplemente no hay camino a merge.
- **Token nunca a disco:** API vía httpx (logs silenciados); git vía
  `-c http.extraHeader="AUTHORIZATION: basic <b64(x-access-token:token)>"` (NO se
  persiste en `.git/config`, NO va en la URL del remoto). El clone privilegiado
  (`panel-clone.sh`, root) recibe el token por **STDIN**, no por argv.
- Verificado con grep exhaustivo: el PAT no aparece en journald, `git log -p`,
  `.git/`, ni en eventos de Postgres; en Config queda cifrado.
- **Sin `git worktree`** por ahora (§5.2): el worker es cola serial y hay una
  clonación por proyecto. Se reevaluará si aparecen sesiones concurrentes sobre
  el mismo repo.

---

## D11 — Bug de aprobaciones "reaparece tras refrescar" (Fase A, MIGRATION1 §2)

### Síntoma (en vivo, 2026-07-20)

> "Le doy Permitir o Denegar y al rato vuelve a aparecer, y además esa
> sesión ya está cerrada."

### Evidencia reproducible en VPS (sesión `561e1cbb-a1a8-4e30-8758-bae3a9f0db24`,
proyecto `plantilla-django-react`)

**Dump inicial** (`PermissionRequest` ordenado por `created_at` desc):
- `94f930a5-…` → `status=pending`, `resolved_by=None`, `exp=17:24:12`,
  `sess_status=stopped`, `expired_now=True`. **Fantasma: lleva horas
  pendiente de una sesión muerta.**
- 14 de las 15 más recientes: todas de sesiones ya `stopped`. La transición
  a `expired` la hace `perm_svc.expire_pending()` solo **al arrancar** el
  worker de la sesión, no al pararla/crash.

**Flujo aprobación actual (`permission_resolve`, vista web)**: hace solo
`SET perm:<uuid>:answer allow|web NX EX 900`. **No toca DB**. El worker es
el único que llama `perm_svc.apply_answer()` al leer la clave — pero si la
sesión está `idle`/`stopped`/`crashed`, **nadie** la lee.

**Confirmación experimental**: creé `REQ_ID=20e0cb4b-…` con
`create_request()` sobre sesión `idle` y luego ejecuté el mismo `SET NX`
que hace la vista. Resultado DB tras el SET:
- `status=pending`, `resolved_by=None` (sin cambios).
- En Redis: `perm:20e0cb4b-…:answer = "allow|web"`.

### Causa raíz

Dos defectos encadenados:

1. **La vista `permission_resolve` no escribe DB.** Solo `SET NX`. La
   actualización a `allowed`/`denied`/`expired` la hace el worker con
   `apply_answer()` desde `_wait_answer()`. Si el worker está esperando
   (`idle`), o si ya murió (`stopped`/`crashed`), esa transición **nunca
   ocurre**.
2. **`expire_pending()` solo se llama al arrancar el worker**
   (`workers/session_worker.py`). `stop_session()` cambia `Session.status`
   pero no cancela las requests pendientes de esa sesión. Resultado: la
   cola web (`/permisos/`) y el badge (`pending_permissions` en
   `context.py`) siguen mostrando fantasmas.

### Filtro de cola también defectuoso

- `panel/ui/views.py:223` — cola:
  `PermissionRequest.objects.filter(status=PENDING)`. **No** filtra
  `expires_at > now()` ni `session.status ∈ {running, waiting_approval,
  idle}`.
- `panel/ui/context.py:13` — badge navbar: misma query cruda, dos
  fuentes sin alinear.

### Hipótesis del §2.1 de MIGRATION1.MD — veredictos

| # | Hipótesis | Veredicto |
|---|-----------|-----------|
| 1 | Resolución no persiste en DB al aprobar | **CONFIRMADA** — vista solo hace SET NX |
| 2 | Cola lista `pending` sin filtrar por sesión viva | **CONFIRMADA** — views.py:223 + context.py:13 |
| 3 | `expires_at` no se respeta en lectura | **CONFIRMADA** — query no incluye filtro de fecha |
| 4 | Doble fuente (DB vs otro query) | **CONFIRMADA** — context.py y views.py divergen; ambas sin filtro |

### Decisión de fix (Fase A, MIGRATION1 §2.2)

- **Una transacción por resolución** (web/telegram/timeout):
  `UPDATE PermissionRequest SET status=?,resolved_by=?,resolved_at=now()
  WHERE id=? AND status='pending'`. Solo si afecta 1 fila se publica al
  worker. Si afecta 0 → conflicto idempotente (otro origen ya respondió).
- **`stop_session`/`crash` cancela en cascada**: en la misma transacción
  que cambia `Session.status`, marcar todas sus `PermissionRequest`
  pendientes como `expired` (status existente) — sin introducir
  `cancelled` nuevo.
- **Una sola query de cola**: `status='pending' AND expires_at > now()
  AND session.status IN ('running','waiting_approval','idle')`, usada
  por vista y badge (helper compartido).
- **Push WS `permission_resolved`/`permission_cancelled`** (reusa el
  canal `perm:resolved` que ya consume `tg_bridge`).

### Compatibilidad

- `apply_answer` y `expire_pending` ya son idempotentes — no se
  reescriben, solo se invoca el patrón desde la vista también.
- El worker sigue leyendo la clave Redis (cambio invisible para él).

---

## D14 — UIEvent v1 normalizado en backend (FASE B)

### Contexto

El chat del panel vuelca el evento crudo del CLI
(`[994] result: {...}`, `system.thinking_tokens: {...}`) — por eso se ve
como un log. OpenHands clasifica cada evento y lo renderiza con un
componente distinto. Hacer eso en el backend con un normalizador
estable (no en el front) da un discriminated union consumible por
cualquier front futuro.

### Contrato UIEvent v1

10 kinds: `agent_text`, `agent_thinking`, `tool_call`, `tool_result`,
`permission_request`, `permission_resolved`, `run_result`,
`session_status`, `git_branch`, `error`. Versión `v: 1` en el JSON para
poder evolucionar sin romper.

### Persistencia dual

- `Event.payload` sigue con el evento crudo (auditoría/replay).
- `Event.ui_event` (JSONField nullable, migración 0006) lleva el
  UIEvent normalizado para el render rápido. Nullable → los eventos
  previos al despliegue y los `system.thinking_tokens` (telemetría)
  quedan en None; el front cae al crudo si es null (backfill nunca
  rompe la UI).

### Streaming

- `ClaudeAgentOptions(include_partial_messages=True)` activa
  `StreamEvent` con deltas token-a-token.
- `StreamAccumulator` por sesión agrupa los deltas por
  `content_block_index` (los stream_event del SDK lo traen).
- `agent_text.streaming=true` se publica SOLO por Redis (efímero, no
  BD) — el cliente lo recibe en vivo y lo descarta cuando llega el
  bloque macro final (que SÍ persiste el UIEvent `streaming=false`).
- El acumulado vive en memoria del worker. Si muere a mitad de un
  stream, el siguiente `AssistantMessage` macro trae el texto
  completo — sin pérdida visible al usuario.

### SDK 0.2.122 (validado en VPS)

Tipos confirmados: `AssistantMessage`, `UserMessage`, `SystemMessage`,
`ResultMessage`, `StreamEvent`. Bloques: `TextBlock`, `ThinkingBlock`,
`ToolUseBlock`, `ToolResultBlock`. La firma `ClaudeAgentOptions` con
todos los campos actuales está documentada en `panel/core/services/
serialize.py`.

### Defensa ante entradas malformadas

- SDK message desconocido → `kind="error"` con el nombre de la clase.
- Bloque desconocido dentro de AssistantMessage → degrada a
  `tool_call` genérico (`generic: true`).
- `tool_result.content` como lista de dicts → se aplana a string si
  todos son `{type: text}`, si no se devuelve la lista.

### Compatibilidad

- Cero cambios en el contrato del consumer WS existente (`seq`,
  `type`, `payload`, `ts` siguen igual; `ui_event` es campo
  adicional).
- Cero cambios en la cola de aprobaciones.
- Regresión 134→151 tests verde. ruff + mypy limpios.

### Cómo regenerar el golden

Si cambias el contrato UIEvent deliberadamente:

```python
python3 /tmp/gen_golden.py  # lee tests/fixtures/normalize_v1.json,
                            # aplica el normalizer, escribe
                            # normalize_v1_golden.json
```

NO a mano — el diff debe revisarse en el PR.
