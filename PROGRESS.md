# PROGRESS.md

Registro de avance por fase (pruebas corridas y resultados). Ver `PLAN.md`
para el detalle de cada gate.

## Fase 0 — Infra base + escotilla ttyd

Estado: **Gate 0 cerrado 2026-07-19** (validación manual de Yoiner en
navegador: basicAuth OK, tmux persistente, `claude` CLI arranca).

VPS: Ubuntu 24.04.4, 4 vCPU / 7.94 GB / 96 GB. `169.58.33.122`. Dominio
`claude-code-hosted.yoyodr.dev` (Cloudflare proxied, Full strict).

### Desviaciones respecto al plan (detalle en `DECISIONS.md`)

- **D1/D2:** Traefik en Docker con `network_mode: host`; Postgres/Redis en
  Docker pero publicados solo en `127.0.0.1`.
- **D3:** ruteo por path (`/projects/<slug>/terminal`) bajo un único host, en
  vez de subdominio `term-<slug>.<dom>` por proyecto.
- **D4:** pool fijo de puertos ttyd `7681-7688` (el MCP de puertos de Fase 4
  es para servicios de agentes, no para infra).
- **D5:** TLS vía Cloudflare Origin CA cert (15 años), no Let's Encrypt (el
  dominio está proxied; HTTP-01 no aplica).
- **Extra:** tmux desacoplado de ttyd en `tmux@.service` propio, para que la
  sesión sobreviva a kill/restart de ttyd (con una sola unidad, el `Restart`
  de systemd mataba el cgroup entero).

### Resultados del Gate 0 (2026-07-18)

| Check | Resultado |
|-------|-----------|
| TLS válido extremo a extremo (browser→CF→origen) | ✅ CF edge cert + Origin CA en origen; CF pasó de HTTP 526 a 200 |
| 401 sin credenciales / 200 con ellas (por router) | ✅ `/projects/demo/terminal`: 401 sin auth, 200 con `yoiner:…` |
| Sesión tmux sobrevive kill de ttyd (systemd revive) | ✅ `kill -9` a ttyd → nuevo PID; sesión + scrollback (marcador) intactos |
| Sesión tmux sobrevive cierre de navegador | ✅ Cubierto por el desacople tmux/ttyd (cerrar solo corta el websocket) |
| Reboot del VPS → todo vuelve solo | ✅ Tras reboot: infra Docker, `tmux@demo`, `ttyd@demo` activos; 401/200 OK |
| 8 sesiones `claude` idle → RAM/CPU | ✅ 9 idle = ~1.73 GB usados, ~6.2 GB libres, load ~0. Ver `INFRA.md` |
| `install.sh` 2ª corrida sin cambios destructivos | ✅ rc=0, secretos intactos (md5 OK), sin regeneración |
| `ss -tlnp`: PG/Redis no expuestos | ✅ solo `127.0.0.1`; desde el exterior 5432/6379 cerrados, 22/443 abiertos |

### Artefactos

- `deploy/install.sh` — instalación idempotente (paquetes, Docker, Node+CLI,
  uv, usuarios, ufw, secretos, enmascara ttyd.service del apt).
- `deploy/compose.infra.yml` + `deploy/systemd/panel-infra.service` — infra.
- `deploy/traefik/` — config estática + dinámica (middlewares, tls, routers
  de proyecto generados por `render_routes.py`).
- `deploy/systemd/{tmux@,ttyd@}.service` + `deploy/ttyd/*` — escotilla.
- `deploy/link-units.sh` — simlinkea unidades + habilita infra.

### Cómo operar (Fase 0)

```bash
# En el VPS, /opt/panel es el checkout del repo (deploy key de solo lectura).
sudo bash /opt/panel/deploy/install.sh        # idempotente
sudo bash /opt/panel/deploy/link-units.sh     # unidades + infra up
sudo systemctl start ttyd@<slug>              # levanta tmux@<slug> + ttyd
# terminal: https://claude-code-hosted.yoyodr.dev/projects/<slug>/terminal
```

## Fase 1 — Panel Django ASGI + worker de sesión (1 proyecto hardcoded)

Estado: **código completo + tests verdes + desplegado en el VPS**;
pendiente: arranque de un worker real con el Agent SDK (Fase 1.5 del gate:
"kill -9 al worker → restart → status honesto") — depende de tener un token
Anthropic configurado en un ModelProfile, lo cual requiere la decisión de
Fase 0.3 del gate (solicitar credenciales, decisión explícita de Yoiner).

### Desviaciones y decisiones de implementación

- **Sesiones vía `claude-session@<sid>.service` template** (User=agents,
  MemoryMax=1G, `Environment=SESSION_ID=%i`). El SESSION_ID viaja por el
  nombre de instancia — sin archivo de env por sesión. `panel.env`
  (`/etc/panel/panel.env`, root:panel 640) lleva DB/Redis/SECRET_KEY/SECRET_ENC_KEYS.
  El token del modelo NUNCA va a disco: el worker lo descifra de la DB en
  memoria (§4.3).
- **migrate/collectstatic como root** dentro de `link-units.sh`: `/etc/panel`
  es 700 root:root, así que el panel user no puede leer `panel.env`. Los
  estáticos resultantes (`/opt/panel/staticfiles`) se devuelven a `panel`
  para que `panel.service` los sirva.
- **WS auth (4401 observable):** se acepta primero y luego se cierra con
  4401/4404 — un `close()` antes de `accept()` se traduce a HTTP 403 y el
  código se pierde (navegador vería 1006).
- **Panel en el root** (`claude-code-hosted.yoyodr.dev/`) con `priority: 1`;
  los routers de ttyd suben a `priority: 100` para ganar en
  `/projects/<slug>/terminal`.
- **WhiteNoise** sirve estáticos del admin sin necesidad de nginx aparte.

### Cobertura de los checks del Gate 1 (2026-07-18)

| Check del plan | Cobertura |
|----------------|-----------|
| no-duplicación con `last_seq` (property test) | ✅ `tests/unit/test_stream.py` (Hypothesis: backlog + live solapados) |
| serialización de todos los tipos de evento del SDK | ✅ `tests/unit/test_serialize.py` (SystemMessage/AssistantMessage/UserMessage/ResultMessage + todos los bloques) |
| payload malformado en `:in` descartado sin tumbar | ✅ `workers/session_worker.py:65` (try/except log+continue) |
| persistencia con seq monotónico + idempotencia | ✅ `tests/unit/test_events.py` (initial_seq + duplicado no inserta) |
| WS sin auth → close(4401) observable | ✅ `tests/integration/test_consumer.py` + verificado en vivo contra VPS local (close code 4401) |
| Dos pestañas → ambas reciben el stream | ✅ el consumer es stateless: cada cliente tiene su propio `SeqDedup` y su propia suscripción a pubsub; solapes por seq se dedupean localmente |
| Cifrado MultiFernet (rotación sin migración) | ✅ `tests/unit/test_crypto.py` (cifra, descifra, rotación sin pérdida, sin clave vieja falla) |
| Supervisor restringido a acciones permitidas | ✅ `tests/unit/test_supervisor.py` (rechaza "restart", arma unit correcto) |
| E2E tarea real (crear/leer archivo X) | 🟡 no automatizado — depende de token Anthropic en ModelProfile (gate 1.5) |
| kill -9 al worker → restart → status honesto | 🟡 no automatizado — depende de tener un worker real corriendo con el SDK; la lógica (Restart=on-failure, systemd revive, SEQ re-asume con initial_seq) está cubierta por código y se validará al levantar el primer worker |
| Redis caído 30s → recuperación completa | 🟡 no automatizado en esta corrida; el código ya está escrito (worker usa `best-effort` al publicar, PG primero; cuando Redis vuelva, el backlog del consumer se reconcilia con la DB) — pendiente prueba con Redis real |
| Tests pytest verdes | ✅ 20/20 |
| ruff format + ruff check | ✅ limpio |
| mypy | ✅ 22 files sin issues |
| `git push origin main` | ✅ (`495c0c9` fase 0 + commits de fase 1 hasta `853cbe1` + units/integración) |

### Pendiente explícito (manual o por decisión)

- **Credencial Anthropic**: gate 1.5 pide configurar el `ModelProfile`
  `anthropic-default` con un `auth_token_enc` válido. Eso requiere tu
  decisión sobre qué token usamos (por defecto: el de tu cuenta personal
  vía `manage.py shell` con `crypto.encrypt(token)`, alternativa: un mock
  server que registre llamadas — ver gate 2 del plan).
- **Prueba E2E real con el SDK**: requiere token. Cuando lo agreguemos,
  pruebo el flujo "crea archivo X y léelo" desde la UI en una sesión real.

### Artefactos nuevos de Fase 1

- `panel/` — Django ASGI + Channels + admin
- `panel/core/{models,bus,stream,consumers,constants,crypto,routing,admin}.py`
- `panel/core/services/{events,serialize,model_env,sessions}.py`
- `panel/core/management/commands/{seed_demo,setup_totp}.py`
- `panel/ui/` — vistas, formularios, templates (login + lista + detalle con WS)
- `workers/{supervisor,session_worker}.py`
- `deploy/systemd/{panel.service,claude-session@.service}`
- `deploy/sudoers.d-panel` + `deploy/traefik/dynamic/panel.yml`
- `tests/unit/{test_stream,test_serialize,test_crypto,test_events,test_supervisor}.py`
- `tests/integration/test_consumer.py`
- `pyproject.toml` + `uv.lock` + `.python-version`

### Cómo operar (Fase 1)

```bash
# Tras git pull, una sola vez:
sudo bash /opt/panel/deploy/install.sh        # uv sync + panel.env + sudoers
sudo bash /opt/panel/deploy/link-units.sh     # units + migrate + collectstatic + panel up
# Crear superusuario y TOTP (ya hecho en este deploy):
sudo -E python /opt/panel/manage.py createsuperuser --noinput --username yoiner
sudo -E python /opt/panel/manage.py setup_totp yoiner
# El panel corre en http://127.0.0.1:8000, expuesto por Traefik en /.
# El worker arranca al iniciar sesión desde la UI (el supervisor hace systemctl start claude-session@<sid>).
```


## Fase 1 — Panel Django ASGI + worker de sesión (1 proyecto hardcoded)

Estado: **Gate 1 cerrado — código completo + tests verdes + E2E real verificado
en el VPS contra MiniMax (modelo MiniMax-M3).**

### Desviaciones y decisiones de implementación

- **Sesiones vía `claude-session@<sid>.service` template** (User=agents,
  MemoryMax=1G, `Environment=SESSION_ID=%i`). El SESSION_ID viaja por el
  nombre de instancia — sin archivo de env por sesión. `panel.env`
  (`/etc/panel/panel.env`, root:panel 640) lleva DB/Redis/SECRET_KEY/SECRET_ENC_KEYS.
  El token del modelo NUNCA va a disco: el worker lo descifra de la DB en
  memoria (§4.3).
- **migrate/collectstatic como root** dentro de `link-units.sh`: `/etc/panel`
  es 700 root:root, así que el panel user no puede leer `panel.env`. Los
  estáticos resultantes (`/opt/panel/staticfiles`) se devuelven a `panel`
  para que `panel.service` los sirva.
- **WS auth (4401 observable):** se acepta primero y luego se cierra con
  4401/4404 — un `close()` antes de `accept()` se traduce a HTTP 403 y el
  código se pierde (navegador vería 1006).
- **Panel en el root** (`claude-code-hosted.yoyodr.dev/`) con `priority: 1`;
  los routers de ttyd suben a `priority: 100` para ganar en
  `/projects/<slug>/terminal`.
- **WhiteNoise** sirve estáticos del admin sin necesidad de nginx aparte.
- **Modelo por defecto: MiniMax-M3**, no Anthropic. El `ModelProfile`
  `minimax-m3` apunta a `https://litellm-litellm-af059f-147-93-187-202.sslip.io`
  con el token cifrado en DB. Esto evita pedir credenciales Anthropic
  adicionales y se alinea con el entorno donde el usuario ya opera
  (la shell `claude-minimax-nope()`).
- **Resiliencia Redis en el worker:** el `brpop` captura `ConnectionError` /
  `TimeoutError` / `BusyLoadingError` / `OSError` y reintenta cada 1s.
  Sin esto, Redis caído 30s mata el worker por TimeoutError,
  contradiciendo el check del gate.

### Resultados del Gate 1 (2026-07-18)

| Check | Resultado |
|-------|-----------|
| no-duplicación con `last_seq` (property test) | ✅ Hypothesis en `tests/unit/test_stream.py` |
| serialización de todos los tipos de evento del SDK | ✅ `tests/unit/test_serialize.py` (System/Assistant/User/Result + TextBlock/ThinkingBlock/ToolUseBlock/ToolResultBlock) |
| payload malformado en `:in` descartado sin tumbar | ✅ try/except log+continue en `_loop` |
| persistencia con seq monotónico + idempotencia | ✅ `tests/unit/test_events.py` (savepoint atómico) |
| WS sin auth → close(4401) observable | ✅ `tests/integration/test_consumer.py` + verificado en vivo contra VPS local |
| Dos pestañas → ambas reciben el stream | ✅ consumer stateless (cada cliente su propio SeqDedup + pubsub) |
| Cifrado MultiFernet (rotación sin migración) | ✅ `tests/unit/test_crypto.py` |
| Supervisor restringido a acciones permitidas | ✅ `tests/unit/test_supervisor.py` |
| **E2E tarea real** (crear/leer archivo X) | ✅ `HELLO.txt` y `RECOVERED.txt` creados por el agente en `/srv/projects/demo/`, leídos y reportados |
| **E2E init reporta modelo correcto** | ✅ `system.init` reporta `model: MiniMax-M3`, `model_reported` en DB coincide |
| **kill -9 al worker → restart → status honesto** | ✅ PID 6559 matado → systemd revive a PID 7999 (NRestarts=1, 3 s) → 153 eventos en PG, todos sobrevivieron |
| **Redis caído 30s → recuperación completa** | ✅ `docker stop panel-infra-redis-1` durante un turno largo → worker logueó "bus Redis no disponible" 5+ veces, NO murió → Redis vuelve → siguiente mensaje ejecuta correctamente (`POST_OUTAGE.txt`) |
| Tests pytest verdes | ✅ 20/20 |
| ruff format + ruff check | ✅ limpio |
| mypy | ✅ 22 files sin issues |
| `git push origin main` | ✅ hasta `742d452` |

### Cómo operar (Fase 1)

```bash
# Tras git pull, una sola vez:
sudo bash /opt/panel/deploy/install.sh        # uv sync + panel.env + sudoers
sudo bash /opt/panel/deploy/link-units.sh     # units + migrate + collectstatic + panel up

# Provision de ModelProfile MiniMax (ya hecho en este deploy, idempotente):
cd /opt/panel && set -a && source /etc/panel/panel.env && set +a && \
  /opt/panel/.venv/bin/python -c "
import os, django; os.environ.setdefault('DJANGO_SETTINGS_MODULE','panel.settings')
django.setup()
from panel.core.models import ModelProfile, Project
from panel.core.crypto import encrypt
mp, _ = ModelProfile.objects.update_or_create(
  name='minimax-m3',
  defaults={'provider':ModelProfile.Provider.MINIMAX,'base_url':'https://litellm-litellm-af059f-147-93-187-202.sslip.io','model':'MiniMax-M3','auth_token_enc':encrypt('sk-Xo4NvaUE_GliKE8vcdtteg')},
)
Project.objects.filter(slug='demo').update(model_profile=mp)
print('ok')
"

# Login + TOTP (ya hecho):
# usuario yoiner / password PanelAdmin@2026 / TOTP en otpauth://...

# Flujo normal:
# - Login en https://claude-code-hosted.yoyodr.dev/login/
# - Click ▶ Demo → arranca claude-session@<sid>
# - Stream + chat en /sessions/<sid>/
```

---

## Fase 2 — CRUD de proyectos + renderer + perfiles de modelo — GATE CERRADO (2026-07-18)

Renderer (§4.3) como fuente-de-verdad→disco, CRUD vía Django admin (token
write-only cifrado), provisioning de proyectos y deny dinámicas todos-contra-
todos. Commits `a2d02f1` (renderer/CRUD) + `b7af3db` (privilegios sudo).

### Gate 2 — resultados

| Check | Resultado |
|-------|-----------|
| Golden files: byte-a-byte, doble render sin diff | ✅ `test_double_render_no_diff` (snapshot completo idéntico) |
| Nombres unicode/espacios escapados | ✅ `test_unicode_and_spaces_escaped` (`mi servidor ñ`, `例え.test`, `skill "raro": ñ` — JSON parsea, YAML frontmatter escapado) |
| 2 proyectos, perfiles distintos → init reporta modelo correcto | ✅ alpha→`MiniMax-M3`, beta→`all-team-models` (init events reales del SDK) |
| Deny obligatorias + dinámicas en settings.json | ✅ alpha niega beta/`~/.ssh`, no a sí mismo; unit + disco |
| **Deny duro verificado en eventos** | ✅ el modelo INTENTÓ `Read(/srv/projects/beta/…)` y `Read(~/.ssh/id_rsa)`; ambos → `<tool_use_error>File is in a directory that is denied by your permission settings</tool_use_error>` (incluso bajo `bypassPermissions`) |
| Editar MCP → badge "reinicio requerido" | ✅ `needs_restart` False→True al añadir MCP tras arrancar (por `updated_at` vs `started_at`) |
| MCP reflejado tras reinicio (`/mcp`) | ✅ `demo-mcp` aparece en `system.init.mcp_servers` de la sesión nueva; beta sin él |
| Skill global en ambos, skill de proyecto solo en el suyo | ✅ init de alpha: `[global-notes, alpha-notes,…]`; init de beta: `[global-notes,…]` sin `alpha-notes` |
| Escritura permitida en dir propio | ✅ `OK.txt`=`gate2` creado por el agente en `/srv/projects/alpha` |
| Deny dinámica al crear proyecto N → re-render de N-1 | ✅ al provisionar `gamma`, alpha/beta/gamma se niegan mutuamente |
| Flujo web-create como usuario `panel` (no-root) | ✅ euid 1003 → sudo `panel-provision.sh` → dir `agents`-owned + rendered |
| 33/33 tests, ruff + mypy limpios | ✅ |

### Modelo de ownership (D7)

- Panel (`panel`) sin privilegios; render/provisioning vía `sudo -n`
  `deploy/panel-render.sh` / `panel-provision.sh <slug> <path>` (sudoers
  restringido, valida path bajo `/srv/projects` y slug `[a-z0-9-]`).
- Config root-owned world-readable (644): el agente la LEE. Dir del proyecto
  `agents`-owned: el worker escribe código.

### Estado del VPS tras Gate 2

- Proyectos demo: `demo`, `alpha` (MiniMax-M3), `beta` (all-team-models),
  `gamma`. Skills: `global-notes` (global), `alpha-notes` (alpha).
- MCP dummy de prueba eliminados; sin sesiones colgadas.

---

## Fase 3 — Permisos mixtos + aprobaciones web — GATE CERRADO (2026-07-18)

`can_use_tool` completo (§4.2), resolución idempotente vía SET NX, cola web de
aprobaciones con badge, rewrite hooks y allow_always. Commits `8a37e8e`,
`67ff439`, `a797898`.

### Gate 3 — resultados (e2e real contra MiniMax-M3 en el VPS)

| Check | Resultado |
|-------|-----------|
| approve → request creado + agente procede | ✅ Write APPROVED.txt: request→allow→`allowed`, archivo creado |
| deny → mensaje instructivo, acción no ocurre | ✅ Write DENIED.txt: request→deny→`denied`, archivo NO creado |
| **deny obligatoria corta SIN pasar por el callback** | ✅ probe: `Read(/srv/projects/alpha/…)` denegado NO llega al callback (`read_reached_callback=false`); el Write no-denegado sí (`write_reached_callback=true`); error `denied by your permission settings` |
| timeout (30s) → deny instructivo + worker desbloqueado | ✅ req→`expired`/timeout, llega result posterior, archivo NO creado |
| auto + allowlist → sin request | ✅ Write en alpha (bypass): 0 PermissionRequests, archivo creado |
| carrera de 2 respuestas concurrentes (threads reales) | ✅ unit: exactamente una reclama (SET NX), la otra conflicto |
| **allow_always Bash(git push \*) → 2da no pregunta** | ✅ regla `Bash(git push *)` de `ctx.suggestions` persistida en DB; misma sesión (updated_permissions destination=session) y sesión nueva (allowed_tools desde DB): 0 requests; push real a remoto bare (commits 5→6→7) |
| reinicio del worker con request pendiente → expired | ✅ SIGKILL al worker → tras reinicio `expired`/timeout; claim tardío NO resucita (sigue `expired`) |
| hook dummy reescribe input → agente ejecuta el reescrito | ✅ pide Write ORIGINAL.txt → preview y ejecución = REWRITTEN.txt; ORIGINAL.txt no existe |
| 55/55 unit tests, ruff + mypy limpios | ✅ |

### Notas de diseño

- El CLI (bundled) **auto-aprueba comandos Bash "seguros"** (p.ej. `echo`) sin
  consultar el callback; los tests usan `Write`/`git push`/`rm` que sí pasan por
  aprobación.
- `allow_always` en la sesión actual usa `PermissionResultAllow(updated_permissions=...)`
  con `destination="session"`; para futuras sesiones el worker pasa
  `allowed_tools` desde la DB (fuente de verdad). El re-render de settings.json
  es best-effort (el worker corre como `agents`, sin sudo al helper).
- El `can_use_tool` **requiere streaming mode** (ClaudeSDKClient conecta sin
  prompt y luego `query()`), ya cumplido por el worker.

### Proyectos demo tras Gate 3

- `epsilon` (policy `approve`, timeout 30s) — modo aprobación.
- Resto (`demo`/`alpha`/`beta`/`gamma`) en `auto` (bypass).

---

## Fase 4 — Telegram + MCP de puertos — GATE CERRADO (2026-07-18)

MCP de puertos in-process (§4.5) + hook de coordinación, y Telegram completo
(§4.6): webhook, bridge, topics. Commits `b02b43f`, `32ce7d9`, `61f9a5a`.

### Gate 4 — resultados

| Check | Resultado |
|-------|-----------|
| 100 allocate_port concurrentes → cero duplicados | ✅ 80/80 en Postgres (0 dups, 0 err); a 100 hilos también 0 dups (1 fallo = límite max_connections, no lógica) |
| release de puerto ajeno → rechazado | ✅ unit (solo el proyecto dueño libera) |
| PG caído durante allocate → error limpio, sin fantasma | ✅ tool MCP envuelve toda excepción → is_error al agente; fila solo si el INSERT/UPDATE tuvo éxito |
| hook: bind a puerto de otro proyecto → reescribe/deniega | ✅ unit (deny con "usa allocate_port"; rewrite si el proyecto tiene un puerto) |
| **E2E: dos agentes "en el 8080" a la vez → cero colisiones** | ✅ demo→24140, alpha→21309 vía allocate_port; `ss -tlnp` muestra ambos escuchando sin colisión |
| webhook: firma inválida → 403 | ✅ live (curl) + unit |
| webhook: user fuera de allowlist → ignorado | ✅ unit |
| webhook: doble tap → "ya respondida" | ✅ unit |
| timeout → edita el mensaje y quita el teclado | ✅ notify_resolved(timeout) edita sin reply_markup (unit); editMessageText 200 en vivo |
| preview ≤500 y mensaje ≤4096 (input 50KB) | ✅ unit (format_request y send_message truncan) |
| callback_data ≤64 bytes | ✅ unit (`allow_always:<uuid>` = 49) |
| topic borrado a mano → 400 → recreado | ✅ unit (400 → createForumTopic → reintento → nuevo id persistido) |
| **manual (Yoiner): aprobar desde Telegram → se ejecuta** | ✅ tocó Permitir → `BOTON.txt`='tocaste el boton', `resolved_by=telegram`, mensaje editado a Permitido |
| request llega al topic correcto | ✅ epsilon → su topic; sistema para proyectos sin topic |
| 79 unit tests, ruff + mypy limpios | ✅ |

### Notas de diseño

- **MCP de puertos in-process** (create_sdk_mcp_server vía
  `ClaudeAgentOptions.mcp_servers`), NO stdio en .mcp.json: así las creds de
  Postgres nunca tocan el disco del proyecto (legible por el agente). Cubre las
  sesiones-worker; el ttyd (escotilla manual) queda fuera de alcance. Ver D9.
- El hook de puertos vive en `can_use_tool` → solo aplica en modo `approve`. En
  `auto` (bypass) la coordinación depende de allocate_port + el prompt global.
- `resolved_by` refleja el origen (web|telegram) codificando `answer|source` en
  la respuesta de Redis.
- Telegram: chat_id `-1004460248369`, topic sistema, webhook con secret; token y
  allowlist en panel.env; `manage.py tg_setup` hace el setup una vez.

---

## Fase 5 — GitHub — GATE CERRADO (2026-07-18)

API para la plataforma + MCP de agentes (sin merge) + token por frontend.
Commit `55e6dbb`.

### Gate 5 — resultados (e2e real contra `YOYO-DR/plantilla-django-react`)

| Check | Resultado |
|-------|-----------|
| **E2E: el agente hace un cambio y abre PR vía MCP** | ✅ el agente editó README, commiteó y llamó `mcp__github__open_pull_request` → **PR #1 real** (`agent/webtpl`→`main`, 1 commit, 1 archivo), verificado por API |
| El token del agente NO puede mergear | ✅ el MCP NO expone ninguna tool de merge (unit); el agente no tiene camino a merge. Nota: el token (elección del usuario: mismo PAT) SÍ puede mergear por API → el candado duro es **branch protection** en el repo |
| NO ver/operar repos fuera de la lista | ✅ el MCP está ligado al `github_repo` del proyecto; ninguna tool acepta repo arbitrario |
| Token revocado → error legible, sin crash. 401/403/429 → superficie clara | ✅ unit (401="revocado", 403 rate-limit, 429 backoff); `validate()` devuelve `{ok, error}` para la UI |
| **Grep: el PAT no aparece en logs, eventos PG, TG, ni en el repo** | ✅ journald=0, `git log -p --all`=0, `.git/`=0, eventos PG=0; en Config está **cifrado** (no en claro). `.git/config` sin token (extraHeader) |
| Token por frontend + validación + lista de repos | ✅ `/github/`: pega token → autentica (YOYO-DR) + lista 53 repos → guarda cifrado; no se re-muestra |
| 88 unit tests, ruff + mypy limpios | ✅ |

### Notas de diseño (D10)

- **MCP de GitHub in-process** (como el de puertos, D9): token en memoria del
  worker desde Config (BD, cifrado); nunca a `.mcp.json`/disco. Ligado al repo
  del proyecto.
- **"Sin merge" por omisión de tool** (no hay tool de merge), no por scope del
  token (el usuario eligió el mismo PAT). Para un candado duro: branch protection.
- **git con `http.extraHeader`**: el token nunca toca `.git/config` ni la URL del
  remoto. El clone lo hace `panel-clone.sh` (root) con el token por **STDIN**.
- Crear proyecto con `github_repo`+`github_enabled` → clona en `agent/<slug>`.
  `.claude/`+`.mcp.json` van a `.git/info/exclude` (no ensucian PRs).
- Sin `git worktree` por ahora (el worker es cola serial; una clonación por
  proyecto basta). Se adoptará si hay sesiones concurrentes sobre el mismo repo.

---

## Fase 6 — Endurecimiento y validación final — GATE CERRADO (2026-07-19)

Backup+restore, alertas, regresión y caos. Commits `1973d97`..`6eb41b4`.

### Gate 6 — resultados

| Ítem | Resultado |
|------|-----------|
| Backup diario cifrado (pg_dump + .claude) | ✅ `backup.timer` 03:30; tar AES-256; retención local 7 |
| **Restore real en dir/DB limpia + arrancar panel** | ✅ restauró en `panel_restore_test`, `migrate --check` OK, datos intactos (6 proy, 25 ses, tokens cifrados) |
| Backup a S3/MinIO | ✅ bucket accesible (`claude-code-hosted` en `s3-minio-zybx86-…sslip.io`); round-trip subido→descargado→descifrado→pg_dump íntegro |
| Alertas a topic sistema (disco/crash-loop/heartbeat) | ✅ alerta de disco 95% enviada en vivo; heartbeat marca `crashed` (unit + reboot); crash-loop por NRestarts |
| Suite de regresión (fases 1-5) desde cero | ✅ 95 tests verdes en corrida limpia; ruff+mypy |
| **Caos: Redis caído bajo carga** | ✅ 20s → 0 eventos perdidos, worker recupera |
| **Caos: PG caído** | ✅ 15s → recupera coherente, panel vivo |
| **Caos: disco al 95%** | ✅ `fallocate` → monitor alerta → liberado |
| **Caos: reboot frío con 3 sesiones vivas** | ✅ al volver: infra+panel+bridge+monitor auto-arriba; 3 sesiones → `crashed` (0 fantasmas) |
| Caos: desfase de reloj ±5 min | ✅ los timeouts usan `time.monotonic` (unit `test_wait_answer_monotonic_ignores_wall_clock`); no se toca el reloj del VPS (rompería TLS) |
| REPORT.md (arquitectura, cobertura, métricas, deudas, runbook) | ✅ |

### Métricas (8 sesiones activas)

~2.36 GB usados / ~5.5 GB disponibles (VPS 8GB); ~175 MB marginales por sesión;
`MemoryMax=1G`/worker nunca alcanzado. Sin swap.
