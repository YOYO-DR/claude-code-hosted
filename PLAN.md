# PLAN.md — Panel Web para Sesiones de Claude Code en VPS (spec técnica v2)

> **Documento para Claude Code (ejecutor).** Tienes acceso root a un VPS de prueba. Este documento define QUÉ construir, CÓMO validarlo y CUÁNDO commitear. Es tu ruta; no la improvises. Si detectas algo que el plan no cubre o un supuesto que resulta falso, **valida contra la realidad (docs oficiales, prueba mínima reproducible) y pregunta a Yoiner antes de desviarte**. Las desviaciones aceptadas se registran en `DECISIONS.md`.

---

## 0. Reglas de trabajo del ejecutor

### 0.1 Flujo Git (obligatorio en cada unidad de trabajo)

El proyecto vive en un repositorio (Yoiner te dará el remote en el arranque; si no existe, **solicítalo** — no crees repos por tu cuenta).

Ciclo por cada unidad de trabajo (una tarea de una fase):

```
implementar → ruff format + ruff check → mypy → pytest (suite completa, no solo lo nuevo)
→ si TODO verde: git add -A && git commit && git push origin main
→ si algo falla: arreglar antes de commitear. NUNCA commit con tests rojos.
```

- Commits en **Conventional Commits**: `feat(fase1): worker de sesión con ClaudeSDKClient`, `fix(fase3): claim atómico en respuestas concurrentes`, `test(fase4): webhook con firma inválida`.
- Un commit = una unidad coherente. No megacommits de fase entera; no commits de archivos a medias.
- `main` siempre debe quedar en estado desplegable: si un cambio requiere migración, el commit incluye la migración y su aplicación probada.
- Al cerrar cada fase: commit final `chore(faseN): gate completo` que actualiza `PROGRESS.md` con las pruebas corridas y sus resultados.
- Nunca commitees: secretos, `.env`, dumps, `node_modules`, artefactos. Configura `.gitignore` en el primer commit y verifica con `git ls-files | grep -E '\.env|secret'` (debe salir vacío) antes de cada push.

### 0.2 Validación y pruebas

- **Los casos listados por fase son el mínimo.** Agrega todo caso límite identificable: input vacío, unicode, payload >1MB, proceso muerto a mitad, dependencia caída, mensajes duplicados, reloj desincronizado. Un caso no probado es un caso fallido.
- Pruebas automatizadas: `pytest` + `pytest-asyncio` + `pytest-django` + `pytest-cov`. Cobertura mínima 80% en `panel/core` y `workers/`; sin mínimo en glue trivial.
- Lo no automatizable (Telegram real, GitHub real, UX en navegador) va a `CHECKLIST-faseN.md` y **esperas confirmación explícita de Yoiner** antes de dar el gate por cerrado.
- Scripts de instalación idempotentes: pruébalos corriéndolos **dos veces** y con `set -euo pipefail`.

### 0.3 Credenciales

- GitHub, Telegram y cualquier API externa las administra Yoiner. En cada punto marcado ➡️ **DETENTE**: enumera exactamente qué necesitas (tipo de credencial, scopes uno a uno, recursos sobre los que aplica, dónde la vas a almacenar y cómo cifrada) y espera respuesta.
- Secretos en runtime: cifrados en DB con Fernet (`SECRET_ENC_KEY` en el env del servicio systemd, archivo `EnvironmentFile=` con permisos `600` root:root) o directamente en `EnvironmentFile` del servicio. Jamás en el repo, logs, eventos persistidos ni mensajes de Telegram. Antes de cada gate: `grep -rE '(ghp_|github_pat_|sk-ant-|[0-9]{9,}:AA)' logs/ panel/ workers/` → vacío.

### 0.4 Convenciones del sistema

- SO objetivo: Ubuntu 24.04. Python 3.12 con `uv` para deps (`uv sync`). Node LTS solo para el CLI de Claude Code.
- Usuario `agents` (sin sudo) corre workers y sesiones. El panel Django corre como usuario `panel`. Root solo para instalación y systemd.
- Rutas: código del panel en `/opt/panel`, proyectos de agentes en `/srv/projects/<slug>`, datos en volúmenes Docker administrados (PG, Redis) o en `/var/lib/` si van nativos (elige y documenta en `DECISIONS.md`; recomendado: PG y Redis en Docker Compose gestionado por systemd, Traefik en Docker).
- Todo servicio propio = unidad systemd con `Restart=on-failure`, `RestartSec=3`, journald como log sink, y `SystemMaxUse=1G` en `journald.conf`.

---

## 1. Arquitectura de referencia

```
                    Internet
                       │
                 ┌─────▼─────┐
                 │  Traefik   │  TLS (LE), routers por Host()
                 └─┬───┬───┬─┘
                   │   │   │
     ┌─────────────▼┐ ┌▼───────────┐ ┌▼──────────────┐
     │ panel.<dom>   │ │term-<slug> │ │ /tg/webhook    │
     │ Django ASGI   │ │ ttyd→tmux  │ │ (ruta en panel)│
     │ (uvicorn)     │ └────────────┘ └────────────────┘
     └──┬────────┬───┘
        │        │ Channels (WS)
   ┌────▼──┐ ┌───▼───┐      ┌──────────────────────────────┐
   │Postgres│ │ Redis │◄────►│ claude-session@<sid>.service │ ×N (≤8)
   └────────┘ └───────┘ pub/ │ workers/session_worker.py    │
                        sub  │ ClaudeSDKClient (Agent SDK)  │
                             └──────┬───────────────────────┘
                                    │ MCP stdio
                          ┌─────────▼─────────┐
                          │ mcp_ports (propio) │──► Postgres (PortRegistry)
                          └────────────────────┘
```

**Decisiones fijas (no re-litigar):**
- Bridge = **Claude Agent SDK (Python)**, no parsing de stream-json a mano ni scraping de TTY.
- La **DB es la fuente de verdad** de toda configuración; un renderer la materializa a los archivos que Claude Code lee. Nadie edita esos archivos a mano.
- Un solo bot de Telegram, supergrupo con topics (uno por proyecto).
- ttyd+tmux se mantiene siempre como escotilla, en paralelo a la UI.

---

## 2. Layout del repositorio

```
/opt/panel (= raíz del repo)
├── PLAN.md  PROGRESS.md  DECISIONS.md  INFRA.md
├── pyproject.toml  uv.lock  .gitignore  .python-version
├── deploy/
│   ├── install.sh              # idempotente; instala deps de SO, docker, crea usuarios
│   ├── traefik/                # traefik.yml + dynamic/*.yml
│   ├── compose.infra.yml       # postgres, redis, traefik
│   └── systemd/
│       ├── panel.service
│       ├── claude-session@.service
│       ├── ttyd@.service
│       ├── tg-bridge.service
│       └── backup.{service,timer}
├── panel/                      # Django project
│   ├── settings.py  asgi.py  urls.py
│   ├── core/                   # apps: projects, sessions, permissions, telegram, github, ports
│   │   ├── models.py  admin.py  api/  consumers.py  renderer.py  services/
│   └── ui/                     # templates + htmx/alpine o SPA ligera (elige, documenta)
├── workers/
│   ├── session_worker.py
│   └── supervisor.py           # arranque/parada de units vía systemctl
├── mcp_ports/
│   └── server.py               # MCP stdio: allocate_port / list_ports / release_port
├── tg_bridge/
│   └── bridge.py               # suscriptor de perm:* → Telegram; webhook handler vive en panel
├── tests/
│   ├── unit/  integration/  e2e/
│   └── conftest.py
└── scripts/
    ├── render_all.py  smoke.sh  chaos/
```

---

## 3. Modelo de datos (Django, app `core`)

Campos esenciales; añade `created_at/updated_at` a todo. Tipos exactos a tu criterio salvo lo indicado.

```python
Project:      slug (unique), name, path, status(active|archived),
              telegram_topic_id (int, null), github_repo (str, null),
              model_profile → FK, permission_policy → FK

ModelProfile: name, provider(anthropic|minimax|custom),
              base_url (null = default), auth_token_enc (BinaryField, Fernet),
              model (str), extra_env (JSONField)

PermissionPolicy: name, mode(auto|approve),
              allowed_tools (JSONField: patrones estilo "Bash(git commit:*)"),
              deny_rules (JSONField)  # se SUMAN a las obligatorias, nunca las reemplazan

Skill:        name, scope(global|project), project (FK null), content (Text),
              enabled (bool)

McpServer:    name, scope(global|project), project (FK null),
              transport(stdio|http), command/args/env o url (JSONField),
              enabled (bool)

Session:      id (UUID), project → FK, status(starting|running|waiting_approval|
              idle|stopped|crashed), sdk_session_id (str, del evento init),
              model_reported (str), total_cost_usd (Decimal), started_at, ended_at

Event:        session → FK, seq (BigInt, monotónico por sesión), type (str),
              payload (JSONField), ts
              # UNIQUE(session, seq) — la reconexión sin duplicados depende de esto

PermissionRequest: id (UUID), session → FK, tool (str), input_full (JSONField),
              input_preview (str, ≤500 chars), status(pending|allowed|denied|
              allowed_always|expired), resolved_by(web|telegram|timeout|null),
              tg_message_id (int, null), expires_at

PortRegistry: port (int, unique), project → FK, purpose (str),
              status(active|released), allocated_by_session (UUID)
```

**Deny rules obligatorias (constante en código, el renderer las inyecta siempre):**

```python
MANDATORY_DENY = [
  "Read(./.env*)", "Read(//home/agents/.ssh/**)", "Read(//home/agents/.claude/**)",
  "Edit(//etc/**)", "Write(//etc/**)", "Read(//opt/panel/**)", "Write(//opt/panel/**)",
  # + por cada proyecto ajeno: Read/Write(//srv/projects/<otro>/**)  ← generado dinámicamente
]
```

Valida la sintaxis exacta de patrones de permisos contra la doc oficial de Claude Code al implementar (➡️ si difiere, ajusta y documenta en `DECISIONS.md`).

---

## 4. Contratos internos

### 4.1 Redis (bus)

```
session:<sid>:in       LIST  (BRPOP por el worker)  {"type":"user_message","text":...}
                                                    | {"type":"interrupt"} | {"type":"shutdown"}
session:<sid>:out      PUBSUB                       Event serializado (mismo que va a PG)
session:<sid>:perm     PUBSUB                       PermissionRequest serializada
perm:<uuid>:answer     STRING SET NX EX 900         "allow" | "deny" | "allow_always"
worker:<sid>:heartbeat STRING EX 15                 timestamp; el panel marca crashed si expira
```

Regla: **Postgres primero, Redis después.** El worker persiste el evento (con `seq`) y luego publica. La UI que se reconecta pide `Event.objects.filter(seq__gt=last_seen)` y luego se suscribe; deduplica por `seq`.

### 4.2 Worker (`session_worker.py`)

```python
# Esqueleto normativo — valida nombres exactos del SDK al implementar
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions

options = ClaudeAgentOptions(
    cwd=project.path,
    permission_mode="bypassPermissions" if policy.mode == "auto" else "default",
    allowed_tools=policy.allowed_tools,
    can_use_tool=can_use_tool_callback,          # solo consultado en zona indecisa
    env=render_env(model_profile),               # ANTHROPIC_BASE_URL / AUTH_TOKEN etc.
    setting_sources=["user", "project"],         # lee lo que el renderer materializó
)
```

- Loop principal: `BRPOP session:<sid>:in` → `client.query(...)` → iterar mensajes del stream → persistir+publicar cada uno → actualizar `Session.status` y costo con el `result`.
- `can_use_tool_callback`: (1) crea `PermissionRequest`; (2) `PUBLISH session:<sid>:perm`; (3) espera `perm:<uuid>:answer` hasta `expires_at`; (4) allow → `PermissionResultAllow` (con `updated_input` si aplica reescritura), deny/timeout → `PermissionResultDeny(message=...)` instructivo; (5) `allow_always` → además persiste la regla en `PermissionPolicy.allowed_tools` y dispara re-render.
- Heartbeat cada 5 s. `SIGTERM` → cierre limpio del cliente, marcar `stopped`.
- El worker NUNCA loguea `input_full` de tools ni env de modelo.

### 4.3 Renderer (`core/renderer.py`)

Función pura `render_project(project) -> dict[path, content]` + aplicador atómico (tmp + `os.replace`). Materializa:

```
/home/agents/.claude/CLAUDE.md               ← prompt general (§7)
/home/agents/.claude/skills/<skill>/SKILL.md
/srv/projects/<slug>/CLAUDE.md
/srv/projects/<slug>/.claude/settings.json   ← permisos (deny obligatorias + policy) SIN env de modelo
/srv/projects/<slug>/.claude/skills/...
/srv/projects/<slug>/.mcp.json               ← mcp_ports global + MCP del proyecto
```

- El env del modelo (tokens) NO va a disco: se inyecta vía `ClaudeAgentOptions.env` desde la DB. Si confirmas que algo solo funciona por `settings.json`, ➡️ pregunta antes de escribir secretos a disco.
- Cambios en `McpServer` o `ModelProfile` → la UI marca la sesión "reinicio requerido" (los MCP no recargan en caliente).

### 4.4 WebSocket (Channels)

```
WS /ws/session/<sid>?last_seq=N
→ servidor: backlog desde PG (seq>N), luego live desde Redis
← cliente: {"type":"user_message"|"interrupt"|"approve","request_id":...,"answer":...}
Auth: sesión Django; sin auth → close(4401).
```

### 4.5 MCP puertos (`mcp_ports/server.py`)

MCP stdio (SDK MCP de Python). Tools:

```
allocate_port(purpose: str) -> {port: int}     # rango 20000-29999, excluye los de INFRA.md
list_ports() -> [{port, project, purpose, status}]
release_port(port: int) -> {ok: bool}          # solo puertos del propio proyecto
```

El slug del proyecto llega por env (`MCP_PROJECT_SLUG`) inyectado por el renderer en `.mcp.json`. Asignación con `SELECT ... FOR UPDATE SKIP LOCKED` o secuencia+unique — cero carreras.

### 4.6 Telegram

- `POST /tg/webhook` en el panel: valida `X-Telegram-Bot-Api-Secret-Token`, filtra `from.id` contra allowlist, procesa solo `callback_query`; `message` sueltos → ignorar.
- `tg_bridge.service`: `psubscribe session:*:perm` → `sendMessage` al `telegram_topic_id` del proyecto con inline keyboard `[Permitir|Denegar|Permitir siempre]`, `callback_data` = UUID (≤64 bytes). Guarda `tg_message_id`.
- Resolución (cualquier origen: web, Telegram, timeout) → `editMessageText` con el desenlace y sin teclado. Doble tap → `answerCallbackQuery("ya respondida")`.
- Texto: `[<slug>] <tool>` + preview ≤500 chars + tiempo restante. Límite duro 4096.

---

## 5. Fases, tareas y gates

> Gate = suite automatizada verde + checklist manual confirmada por Yoiner + commits pusheados. Orden estricto 0→6.

### Fase 0 — Infra base + escotilla ttyd

➡️ **DETENTE al inicio.** Solicita a Yoiner: (a) remote del repo y método de push (probablemente PAT solo-este-repo con `contents:rw` — confírmalo con él); (b) dominio/subdominios y cómo apuntan al VPS; (c) email para Let's Encrypt.

1. `deploy/install.sh`: paquetes SO, Docker, usuarios `agents`/`panel`, `uv`, Node + `@anthropic-ai/claude-code`, tmux, ttyd, estructura de dirs.
2. `compose.infra.yml`: Traefik (LE, dashboard off), Postgres 16, Redis 7 (AOF on). Unidad systemd que hace `docker compose up -d` al boot.
3. `ttyd@.service`: instancia `ttyd@<slug>` = ttyd (basicAuth desde archivo credencial) adjunto a `tmux new -A -s cc-<slug>`, router Traefik `term-<slug>.<dom>`.
4. `INFRA.md`: puertos reservados por la plataforma, RAM/CPU base.

**Gate 0:**
- [ ] TLS válido; 401 sin credenciales y 200 con ellas en cada router (curl).
- [ ] Sesión tmux sobrevive a: cierre de navegador, kill de ttyd (systemd lo revive), reboot del VPS.
- [ ] 8 tmux con `claude` idle → RAM/CPU registrados en `INFRA.md`. Si no aguanta, DETENTE y reporta.
- [ ] `install.sh` corrido 2ª vez: sin cambios destructivos.
- [ ] `ss -tlnp`: Postgres y Redis NO expuestos fuera de la red Docker.

### Fase 1 — Panel Django + worker de sesión (1 proyecto hardcoded)

1. Django ASGI (uvicorn tras Traefik en `panel.<dom>`), Channels con Redis layer, auth Django + TOTP (django-otp), un superusuario.
2. Migraciones de TODOS los modelos de §3 (aunque algunos se usen después).
3. `session_worker.py` per §4.2 + `claude-session@.service` (`User=agents`, `EnvironmentFile` por sesión generado por el supervisor, `MemoryMax=1G`).
4. `supervisor.py`: start/stop/status vía `systemctl`; sudoers restringido SOLO a `systemctl {start,stop,status} claude-session@*` para el usuario `panel`.
5. UI: vista de sesión con stream (backlog + live per §4.4), input de chat, estado, costo acumulado.

**Gate 1:**
- [ ] pytest: no-duplicación con `last_seq` (property test con secuencias aleatorias); serialización de todos los tipos de evento del SDK (fixture grabada de una sesión real); payload malformado en `:in` descartado sin tumbar el worker.
- [ ] E2E: tarea "crea archivo X y léelo" verificada en disco y en UI.
- [ ] `kill -9` al worker a mitad de tarea → restart → status honesto (`crashed`→`running`), cero eventos perdidos en PG.
- [ ] Redis caído 30 s durante streaming → recuperación completa.
- [ ] WS sin auth → close(4401). Dos pestañas → ambas reciben el stream.

### Fase 2 — CRUD de proyectos + renderer + perfiles de modelo

1. CRUD web: Project, ModelProfile (token cifrado, nunca se re-muestra), Skill, McpServer, PermissionPolicy.
2. `renderer.py` per §4.3 + `scripts/render_all.py`. Crear proyecto = dir + `git init` + render + worker up. Archivar = worker down + datos intactos.
3. Deny obligatorias dinámicas: al crear el proyecto N, re-render de los N-1 (cada uno niega los dirs de los demás).

**Gate 2:**
- [ ] Golden files del renderer: byte a byte, doble render sin diff, nombres con unicode/espacios correctamente escapados.
- [ ] 2 proyectos con perfiles distintos → el evento `init` de cada uno reporta el modelo correcto. Para MiniMax: ➡️ solicita la key a Yoiner; si prefiere no darla aún, usa un mock server local que registre el `base_url` golpeado.
- [ ] Pedir explícitamente al agente A leer `/srv/projects/b/` y `~/.ssh` → deny verificado en eventos.
- [ ] Editar MCP → badge "reinicio requerido" → tras reinicio, `/mcp` refleja el cambio.
- [ ] Skill global visible en ambos proyectos; skill de proyecto solo en el suyo.

### Fase 3 — Permisos mixtos + aprobaciones web

1. `can_use_tool_callback` completo per §4.2 (timeout default 15 min, configurable por proyecto).
2. Claim atómico `SET NX` per §4.1; resolución idempotente.
3. UI: cola de pendientes (badge global) con Permitir / Denegar / Permitir siempre ("siempre" persiste la regla y re-renderiza).
4. Mecanismo de reescritura: `rewrite_hooks: list[Callable]` en el callback, probado aquí con un hook dummy (el hook real de puertos llega en Fase 4).

**Gate 3:**
- [ ] Unit: `auto`+allowlist → sin request; `approve` → request; deny obligatoria corta SIN pasar por el callback; timeout → deny instructivo y worker desbloqueado.
- [ ] Carrera de 2 respuestas concurrentes (threads reales) → una gana, la otra recibe conflicto.
- [ ] "Permitir siempre" sobre `Bash(git push:*)` → segunda invocación no pregunta (e2e con push real a repo dummy local).
- [ ] Reinicio del worker con request pendiente → queda `expired`, jamás zombie aprobable.
- [ ] Hook dummy reescribe input y el agente ejecuta el input reescrito (verificado en eventos).

### Fase 4 — Telegram (bot único + topics) + MCP de puertos

➡️ **DETENTE.** Solicita a Yoiner: bot token de @BotFather; que cree el supergrupo con topics y agregue el bot como admin (manage topics); su user_id para la allowlist. Captura el `chat_id` una vez vía `getUpdates` y persístelo.

1. `tg_bridge.service` + webhook per §4.6. `setWebhook` con `secret_token` aleatorio persistido.
2. Crear proyecto → `createForumTopic`, guardar `message_thread_id`. Topic "sistema" para alertas.
3. `mcp_ports/server.py` per §4.5, montado global vía renderer.
4. Hook real de puertos en `can_use_tool`: detectar binds (`-p`, `--port`, puertos en compose) → si el puerto es de otro proyecto: reescribir al asignado si existe o denegar con "usa allocate_port".

**Gate 4:**
- [ ] Unit: firma de webhook inválida → 403; user fuera de allowlist → ignorado; doble tap → "ya respondida"; timeout edita el mensaje y quita el teclado; preview trunca a 500 y mensaje nunca >4096 (test con input de 50KB); callback_data ≤64 bytes.
- [ ] MCP: 100 `allocate_port` concurrentes → cero duplicados; release de puerto ajeno → rechazado; PG caído durante allocate → error limpio al agente, sin puerto fantasma.
- [ ] Topic borrado a mano → error 400 capturado → topic recreado.
- [ ] **Checklist manual con Yoiner:** aprobar desde Telegram → se ejecuta; denegar; carrera web-vs-Telegram; texto suelto sin pending → nada; request llega al topic correcto.
- [ ] E2E: dos agentes piden levantar servicio "en el 8080" a la vez → `ss -tlnp` muestra cero colisiones.

### Fase 5 — GitHub (MCP para agentes, API para plataforma)

➡️ **DETENTE.** Presenta a Yoiner opciones con pros/contras y espera su decisión: (1) PAT fine-grained de **plataforma** — enumera los repos exactos y permisos: `contents:rw`, `metadata:r`, `pull_requests:rw`, y pregúntale si necesitará crear repos (`administration:rw` solo en ese caso); (2) credencial para el **GitHub MCP de agentes** — PAT separado sin capacidad de merge (recomendado) vs mismo PAT vs `gh auth login` en el VPS; (3) repo de prueba para el e2e.

1. `core/services/github.py` (httpx, API REST): clone (token vía credential helper temporal, jamás en `.git/config`), crear rama, estado de PRs para la UI.
2. Crear proyecto desde repo: clone → rama `agent/<slug>` → evalúa `git worktree` por sesión concurrente sobre el mismo repo (si lo adoptas, documenta) → render → worker.
3. GitHub MCP inyectado por el renderer solo en proyectos con `github_repo` y flag activo.

**Gate 5:**
- [ ] E2E con el repo de prueba: el agente hace un cambio y abre PR vía MCP → PR real verificada.
- [ ] El token del agente NO puede mergear (intento → 403 verificado) ni ver repos fuera de la lista.
- [ ] Token revocado → error legible en UI, sin crash. 401/403/429 → backoff y superficie clara.
- [ ] Grep exhaustivo: el PAT no aparece en logs, eventos PG, mensajes TG, ni en el repo (`git log -p` incluido).

### Fase 6 — Endurecimiento y validación final

1. `backup.timer` diario: `pg_dump` + `/home/agents/.claude` + configs de `/srv/projects/*/.claude` → tar cifrado local (➡️ pregunta a Yoiner si quiere destino remoto). **Probar un restore real** en dir limpio y arrancar el panel contra el dump restaurado.
2. Alertas al topic "sistema": worker en crash-loop (≥3 restarts / 5 min), disco >90%, heartbeat perdido.
3. Suite completa de regresión (fases 1-5) verde en una corrida limpia desde cero.
4. Caos (`scripts/chaos/`): reboot frío con 3 sesiones a mitad de tarea → estado honesto al volver; disco al 95% (fallocate) → workers fallan limpio y alertan; `docker stop` de Redis y de PG por separado bajo carga → recuperación sin corrupción; desfase de reloj ±5 min → timeouts de permisos siguen correctos (usa monotonic donde aplique).
5. `REPORT.md`: arquitectura final construida, cobertura, métricas con 8 sesiones activas, deudas conocidas, y runbook de operación (arrancar, parar, restaurar backup, rotar tokens).

---

## 6. Consideraciones y trampas conocidas (léelas antes de cada fase)

- **SDK:** los nombres exactos (`ClaudeAgentOptions`, tipos `PermissionResult*`, `setting_sources`) pueden diferir según versión. En Fase 1: instala, lee la referencia del paquete instalado y ajusta. Si cambia un contrato de §4 → `DECISIONS.md`.
- **`allowed_tools` en `bypassPermissions` no restringe nada** (todo está permitido); solo importa en `default`. Las deny sí aplican siempre. No inviertas esa lógica.
- **Un `query()` a la vez por sesión:** el worker es cola serial; mensajes que llegan durante ejecución se encolan y la UI los muestra "en cola". `interrupt` debe funcionar en ambos estados.
- **`seq` lo asigna el worker**, no la DB (autoincrement global rompería el orden por sesión). Contador en memoria inicializado con `MAX(seq)+1` al arrancar.
- **Workers y tmux son mundos separados:** las sesiones del SDK NO viven en tmux; ttyd/tmux es solo la escotilla manual. No intentes unificarlos.
- **Fernet:** usa `MultiFernet` desde el día uno para poder rotar claves sin migración dolorosa.
- **No expongas Postgres/Redis** fuera de la red interna de Docker. Se verifica en gates 0 y 6.
- Si en cualquier punto detectas que algo del plan ya lo resuelve una feature nueva de Claude Code (p. ej. server mode / remote control evolucionaron), **no lo adoptes en silencio**: repórtalo con evidencia y espera decisión.

---

## 7. Prompt general (seed de `~/.claude/CLAUDE.md`, editable luego desde la web)

```markdown
# Reglas globales — todos los agentes de este VPS

- Compartes este VPS con otros agentes en otros proyectos. Acceso amplio ≠ permiso para todo: criterio primero.
- PUERTOS: nunca elijas puerto a mano. MCP `ports`: `allocate_port` antes de exponer cualquier servicio, `list_ports` para consultar, `release_port` al desmontar. Puerto ocupado = de otro agente: pide otro, jamás mates procesos ajenos.
- No detengas, reinicies ni modifiques contenedores, servicios o procesos que no sean de tu proyecto. En duda, genera solicitud de aprobación.
- Trabaja solo dentro de tu directorio. Otros proyectos, ~/.ssh, ~/.claude y /etc están denegados: no intentes rodear el deny.
- Docker: prefija contenedores, redes y volúmenes con el slug de tu proyecto.
- Si una aprobación expira sin respuesta, continúa con lo que no la requiera o deja el trabajo limpio y documentado en NOTES.md.
- Registra en NOTES.md de tu proyecto los recursos compartidos que uses y el estado que otros agentes deban conocer.
- Git: ramas + PRs. Nunca push directo a main/master sin aprobación explícita.
```

---

## 8. Arranque

1. Lee §0 y §6 completos.
2. Ejecuta la DETENCIÓN inicial de Fase 0 (repo, dominio, email LE).
3. Construye fase a fase con el ciclo git de §0.1: implementar → lint/type/test → verde → commit → push a main.
4. En cada ambigüedad real: valida contra docs o prueba mínima reproducible → si sigue ambiguo, pregunta a Yoiner.
5. Ningún gate se cierra sin la confirmación manual de Yoiner donde aplique, y ninguna fase arranca con el gate anterior abierto.