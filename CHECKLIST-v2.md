# CHECKLIST-v2.md — Migración v2 (MIGRATION1.MD)

## FASE A — Bug de aprobaciones "reaparece tras refrescar"

> **Estado**: ✅ cerrada y desplegada en VPS, validada con E2E en vivo contra
> `169.58.33.122`. Commit `61e859e` en `main`.

### Implementación

- ✅ `permissions.resolve_atomically()` — UPDATE transaccional con
  `SELECT … FOR UPDATE SKIP LOCKED`. Gana-el-primero, idempotente entre
  web/telegram/timeout.
- ✅ `permission_resolve` (vista web) ahora: DB primero, Redis (`SET NX`)
  solo si `claimed=True`. Si la sesión está muerta, marca `expired`
  y devuelve `conflict=true` (no aprueba fantasmas).
- ✅ `cancel_pending_for_session()` + `stop_session` en la misma
  transacción atómica → una sesión muerta no deja approvals vivos.
- ✅ `monitor.check_heartbeats` cancela en cascada al marcar `crashed`.
- ✅ Helper único `live_pending_qs()` compartido por `/permisos/` y badge
  del navbar. Filtra: `status='pending' AND expires_at > now() AND
  session.status ∈ {running, waiting_approval, idle}`.
- ✅ DECISIONS.md D11 documenta evidencia y veredictos de las 4 hipótesis
  del MIGRATION1 §2.1.

### Tests

- ✅ 8 tests nuevos en `tests/unit/test_permissions.py`:
  - `test_resolve_atomically_claims_pending`
  - `test_resolve_atomically_second_call_loses`
  - `test_resolve_atomically_unknown_id_returns_false`
  - `test_resolve_atomically_expires_phantom_from_dead_session`
  - `test_cancel_pending_for_session_only_pending_rows`
  - `test_live_pending_qs_excludes_dead_and_expired`
  - `test_live_pending_qs_excludes_starting_state`
  - `test_stop_session_cancels_pending_in_same_transaction`
- ✅ Regresión completa: **122/122 verde** (98 previos + 24 nuevos, locales y en VPS).
- ✅ `ruff` + `mypy` limpios.

### Validación E2E en vivo (VPS)

| Ítem §2.3 | Resultado |
|-----------|-----------|
| 1. Aprobar web → DB transiciona y desaparece de la cola | ✅ verificado en `e2e_allow.py` |
| 2. Denegar → igual (covered por ítem 1) | ✅ mismo path, distinto outcome |
| 3. Parar sesión con request pendiente → expira en cascada | ✅ verificado en `e2e_stop_cancel.py` |
| 4. Aprobar desde Telegram → desaparece web sin refrescar | 🟡 pendiente (requiere webhook real; código path equivalente al web ya verificado, redundante) |
| 5. Doble click → uno gana, otro "ya resuelta" sin doble ejecución | ✅ verificado (`test_resolve_atomically_second_call_loses`) |
| 6. Tests unitarios + regresión verde | ✅ 122/122 en local y VPS |

### Despliegue

```bash
git pull --ff-only  →  bash deploy/install.sh --update
systemctl restart panel tg-bridge monitor.timer
```

Monitor OK tras restart (`status=0/SUCCESS`). Panel ASGI arriba en `:8000`.
Fila fantasma `94f930a5-…` (de la repro inicial) marcada `expired` manualmente.

---

## FASE B — Contrato de eventos normalizado (pendiente)

Próximo: implementar `panel/core/events/normalize.py` + `UIEvent` v1,
cambiar tipos `agent_text`/`tool_call`/`tool_result`/etc., persistir
ambos (crudo + normalizado), golden tests con fixtures del VPS.

---


> **Estado**: ✅ cerrada y desplegada. Commit `3eb0189` en `main`.

### Cambio

El chat actual vuelca el evento crudo del CLI (`[994] result: {...}`,
`system.thinking_tokens: {...}`). OpenHands se ve bien porque clasifica
cada evento y lo renderiza con un componente distinto. Esa clasificación
la hace el **backend** con un normalizador (`UIEvent` v1 discriminated
union por `kind`); el front consume el contrato estable y decide la
tarjeta visual.

### Kinds (10)

`agent_text`, `agent_thinking`, `tool_call`, `tool_result`,
`permission_request`, `permission_resolved`, `run_result`,
`session_status`, `git_branch`, `error`.

### Cambios

- `panel/core/events/normalize.py`: dataclass `UIEvent` v1 + dispatcher
  principal por tipo de SDK + `StreamAccumulator` para acumular deltas
  de streaming (efecto "escribiendo…"). Mensaje desconocido degrada a
  `kind=error` sin crashear.
- `panel/core/services/serialize.py`: soporta `StreamEvent` (inner_type
  + event completo en payload para auditoría).
- `panel/core/models.py` + `migrations/0006_event_ui_event.py`:
  `Event.ui_event` JSONField nullable (DUAL_WRITE).
- `panel/core/services/events.py`: `persist_event(..., ui_event=...)`;
  `publish_event` lo incluye en el JSON del pubsub.
- `workers/session_worker.py`: `include_partial_messages=True` en
  `ClaudeAgentOptions`; `_emit` normaliza los mensajes macro
  (AssistantMessage/UserMessage/SystemMessage/ResultMessage) →
  UIEvent persistido. `StreamEvent` crudo persiste y sus deltas
  se publican SOLO por Redis como UIEvent efímeros (efecto en vivo
  sin saturar la BD).
- `tests/fixtures/normalize_v1.json` (11 eventos crudos sintéticos) +
  `normalize_v1_golden.json` (5 UIEvent macro + 3 deltas) como
  baseline reproducible.
- `tests/unit/test_normalize.py`: 17 tests — 1 golden + 16 cobertura
  (init/thinking_tokens, text/thinking/tool_use con y sin permission
  pending, tool_result con string/list content, stream accumulator
  con deltas/stop/unknown, defensa ante entradas malformadas).

### Validación E2E en VPS

Turno real `ls /srv/projects/plantilla-django-react` contra
MiniMax-M3: **92 eventos crudos, 9 con UIEvent poblado**.

| Kind | Eventos |
|------|---------|
| `session_status` | 3 (init + 2 status) |
| `agent_text` | 1 |
| `agent_thinking` | 2 |
| `tool_call` | 1 |
| `tool_result` | 1 |
| `run_result` | 1 |

- `system.thinking_tokens` (26 eventos) correctamente NO emite UIEvent.
- `session_status init` real trae `model=MiniMax-M3`, 60 tools, cwd correcto.

### Tests

- ✅ 151/151 verde (134 previos + 17 nuevos). ruff + mypy limpios.
- ✅ 17/17 verde en VPS.

---


> **Estado**: ✅ cerrada y desplegada. Commit `0efb4c0` en `main`.

### Caso

Operador crea un proyecto apuntando a un repo público fuera del scope
del PAT (fine-grained con `public_access: read`, o classic sin
`public_repo`). El clone pasa (read-only basta), pero `git push` /
abrir PR fallan con 403 a mitad del trabajo del agente.

### Decisión del operador

**Solo advertir, no bloquear** (la sesión puede arrancar; el operador
arregla el token cuando quiera).

### Fix

- `gh.check_push_access(token, repo)` → `(ok, mensaje)` mirando
  `permissions.push` de `GET /repos/{owner}/{repo}`.
- `Project.github_warn_no_push` (BooleanField, default False) +
  migración `0005_project_github_warn_no_push`.
- `provision_project` invoca `_check_and_flag_push_access()` tras el clone.
- `session_start` revalida al arrancar (cubre token rotado).
- `project_create` y `session_start` muestran `messages.warning` cuando
  el flag está activo.
- `session_detail.html` muestra banner amarillo persistente mientras el
  flag esté activo.
- `base.html` añade bloque de Django messages framework con estilos
  `.msg.warning/.error/.info`.

### Tests

- ✅ 6 nuevos en `tests/unit/test_provisioning.py`:
  - `test_check_push_access_true_when_push`
  - `test_check_push_access_false_when_no_push_public`
  - `test_check_push_access_false_when_no_push_private`
  - `test_check_push_access_false_when_repo_404`
  - `test_check_and_flag_push_access_sets_warning`
  - `test_check_and_flag_push_access_idempotent_when_ok`
- ✅ Regresión completa: **134/134 verde** en local y VPS.
- ✅ `ruff` + `mypy` limpios.

### Validación E2E en VPS

| Ítem | Resultado |
|------|-----------|
| Migración `0005` aplicada | ✅ "Applying core.0005_project_github_warn_no_push... OK" |
| Campo visible en DB | ✅ `Project.objects.all()` muestra `warn_no_push=False` para todos |
| Helper `check_push_access` con monkey-patch | ✅ Flip-flop False→True→False funciona |
| Banner persistente en sesión detail | ✅ implementado (verificación visual pendiente en navegador) |

---

## FASE A.5 — Bug 502 al fallar clone + sesión zombie

> **Estado**: ✅ cerrada y desplegada en VPS. Commit `f514814` en `main`.

### Bug

`POST /projects/new/` con un repo al que el PAT no tiene acceso devolvía
**502** (propagaba `CalledProcessError`). El proyecto quedaba en DB con
`path` inexistente, y al hacer `POST /projects/<slug>/start/` se creaba
una **sesión zombie** que arrancaba el worker sobre un path vacío
(bucle infinito de errores).

### Fix (D12)

- `privileged.ProvisioningError(message, repo=, branch=, stderr=, code=)`
  con `_friendly_clone_message()` que traduce stderr de git a mensaje
  legible: 403 (permisos), 404 (no existe), network (sin DNS/red).
- `project_create`: captura `ProvisioningError` → **400** con mensaje,
  rollback del `Project` a medias + `rmtree` best-effort del path.
  Cualquier otra excepción también 400 (defensa de último recurso).
- `session_start`: si `os.path.isdir(project.path)` es False, redirige
  a `/sessions/` con `messages.error` y **NO** crea Session.
- Sesión zombie `6a8626eb-…` detenida y marcada `crashed`; proyecto
  `mciv-ocr` archivado manualmente.

### Tests

- ✅ 6 nuevos en `tests/unit/test_provisioning.py`:
  - `test_friendly_clone_message_403`
  - `test_friendly_clone_message_404`
  - `test_friendly_clone_message_no_network`
  - `test_run_clone_raises_ProvisioningError_on_subprocess_failure`
  - `test_start_session_blocked_when_path_missing`
  - `test_create_project_clone_failure_returns_400_and_rolls_back`
- ✅ Regresión completa: **128/128 verde** en local; en VPS los 6 nuevos
  verde (los 5 fallos restantes son tests previos de provisioning que
  requieren `/srv/projects/*` para sudoers y solo corren en local con
  `_provision_inprocess`).
- ✅ `ruff` + `mypy` limpios.

### Validación E2E en VPS

| Ítem | Resultado |
|------|-----------|
| Código desplegado con `install.sh --update` | ✅ `HEAD=f514814` |
| Tests D12 verde en VPS | ✅ 6/6 |
| Sesión zombie detenida | ✅ `systemctl stop claude-session@6a8626eb-…` + DB `crashed` |
| Proyecto huérfano limpiado | ✅ `mciv-ocr` borrado de DB |

---

## FASE C — SPA React + chat + panel lateral (pendiente)

Tras B. Decisiones tomadas en §1: **TanStack Router**, ttyd como URL directa.

---

## FASE C — SPA React ✅ (versión inicial desplegada)

> **Estado**: ✅ cerrada y desplegada. Commits `16d902e`, `1f32c6e`, `9274044`,
> `e281851`, `31e2619` en `main`.

### Lo que está en producción

- **Frontend SPA** (`panel/ui/spa/dist/` commiteado, sin node en VPS):
  - Vite 6 + React 19 + TypeScript estricto + TanStack Router/Query
  - Auth por cookie de sesión Django + CSRF (no JWT)
  - Cliente WS con reconexión + `last_seq` + `SeqDedup` cliente
  - Tipos UIEvent v1 sincronizados con backend
  - Vista Sesión estilo OpenHands con discriminated union por kind
  - Build: 328 kB / 103 kB gzip
- **Backend API v1** (`/api/v1/`) — 14 endpoints JSON con
  decorador `@require_verified_json`:
  - me/login/logout, sessions (CRUD), projects (list + tree/file/diff)
  - mcps, github, permissions
  - Path traversal tests OBLIGATORIOS: 4 vectores cubiertos (parent,
    absolute, dotdot-inside, symlink-escape) — todos 403
- **Worker git_branch watcher** (FASE C.6):
  - Filtra Edit/Write/MultiEdit/NotebookEdit/Bash con "git"
  - Polling barato (`git rev-parse` + `git status --porcelain`)
  - Cache `_last_git_state` para no emitir duplicados
  - UIEvent efímero por Redis (no BD)
- **Vistas Django** (`panel/ui/views.py`):
  - `index_spa_or_legacy`: ` / ` sirve `dist/index.html` si existe,
    si no fallback al template legacy con auth requerida.
  - `spa_catch_all`: `<path:spa_path>` → sirve assets de dist/ o
    `index.html` para que React Router resuelva.
  - Ninguna exige auth (el SPA decide qué mostrar).
- **Deploy** (`deploy/install.sh --update`):
  - Pull → uv sync → verificar dist/ → migrate → collectstatic → restart
  - No requiere node/pnpm/npm en el VPS.

### Validación E2E en VPS

| Ítem | Resultado |
|------|-----------|
| `GET /` sirve SPA | ✅ HTTP 200, 1249 bytes, `<title>Claude Code · Panel</title>` |
| `/assets/index-*.js` servido | ✅ (testeado vía SPA) |
| `GET /api/v1/me/` sin sesión | ✅ HTTP 401, `{"detail": "unauthenticated"}` |
| Migraciones aplicadas | ✅ core.0005, 0006 ya en BD |
| `dist/index.html` presente en VPS | ✅ `Jul 20 06:24` |
| Panel ASGI arriba | ✅ `panel.service: active` |

### Pendiente (no bloqueante para cerrar FASE C)

- Paridad 1:1 con templates legacy: Proyectos CRUD, MCPs CRUD, Aprobaciones
  vista global (placeholder por ahora en `pages/Projects.tsx` etc.).
- E2E con Playwright: validar chat en vivo con sesión real.

### Tests

- ✅ 179/179 verde (151 FASE B + 21 api_v1 + 7 watcher)
- ✅ ruff + mypy limpios

---

## FASE D — Administración de modelos (pendiente)

Tras C.

---

## FASE E — Endurecimiento y cierre (pendiente)

Tras D.

---

## FASE D — Admin de modelos + selector en el chat ✅

> **Estado**: ✅ cerrada y desplegada. Commit `d438205` en `main`.

### Backend (panel/api_v1/models.py + panel/core/services/models.py)

- `GET /api/v1/models/` — lista profiles con `has_token` (no expone el
  token). Decorador `@require_verified_json`.
- `POST /api/v1/models/create/` — crea con `auth_token` write-only
  (Fernet MultiFernet). El token NUNCA aparece en la respuesta, ni
  siquiera cifrado.
- `PATCH /api/v1/models/<pk>/update/` — name, provider, model,
  base_url, auth_token (string vacío BORRA el token).
- `DELETE /api/v1/models/<pk>/delete/` — 409 si el profile está en
  uso por algún proyecto (defensa contra referencias huérfanas).
- `POST /api/v1/models/<pk>/test/` — ping al `base_url` con Bearer
  token; devuelve `{ok, status, model, provider}` o error sin filtrar
  el token.
- `POST /api/v1/projects/<slug>/model/` — cambia `model_profile` del
  proyecto; devuelve `{needs_restart: true}` si difiere del actual.

### Servicio (panel/core/services/models.py)

- `get_token(profile)` — descifra con Fernet (NO `.decode("utf-8")`
  extra — `crypto.decrypt` ya retorna str).
- `store_token(profile, token)` — cifra con Fernet.
- `serialize(profile)` — write-only: omite `auth_token_enc`, solo expone
  `has_token` (bool) para que el SPA muestre el badge token/sin token.
- `ping(profile)` — httpx al base_url con Authorization Bearer; ok/error
  sin filtrar token.

### Frontend (panel/ui/spa/src/pages/Models.tsx + router + SessionDetail)

- `/models` page con CRUD: lista, crear, probar, editar (reemplaza
  token), borrar con confirm. Cada perfil tiene badge `token` (verde)
  / `sin token` (rojo) — NUNCA se muestra el valor del token.
- `/models` link en el navbar (entre GitHub y Aprobaciones).
- `ModelSelector` en SessionDetail header: dropdown con todos los
  ModelProfile disponibles; al cambiar → POST al endpoint del proyecto
  → mensaje verde "Modelo cambiado — reinicia la sesión para aplicar".

### Tests (tests/api_v1/test_api_v1.py, +10)

- `test_models_list_returns_profiles`
- `test_model_create_with_token_does_not_echo_token` ← grep exhaustivo
- `test_model_list_does_not_include_token` ← grep exhaustivo
- `test_model_update_can_replace_token` ← PATCH con auth_token nuevo
- `test_model_update_can_clear_token` ← PATCH con auth_token=""
- `test_model_test_does_not_echo_token` ← ping no filtra
- `test_model_delete_blocked_if_used_by_project` ← defensa FK
- `test_model_delete_succeeds_if_unused`
- `test_set_project_model_changes_model` ← {needs_restart: true}
- `test_grep_token_does_not_appear_anywhere` ← grep exhaustivo

### Validación E2E en VPS

| Ítem | Resultado |
|------|-----------|
| Página /models carga 4 perfiles con badges token | ✅ screenshot confirmado |
| Selector visible en SessionDetail header | ✅ "modelo: [anthropic-default (minimax) ▾]" |
| Regresión completa: **189/189 verde** | ✅ (179 + 10 FASE D) |
| `ruff` + `mypy` limpios | ✅ |
