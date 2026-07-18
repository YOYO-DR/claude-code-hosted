# PROGRESS.md

Registro de avance por fase (pruebas corridas y resultados). Ver `PLAN.md`
para el detalle de cada gate.

## Fase 0 — Infra base + escotilla ttyd

Estado: **gate automatizado verde**; pendiente confirmación manual de Yoiner
(ver `CHECKLIST-fase0.md`).

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
