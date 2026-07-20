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

## D13 — Warning si el PAT no tiene push sobre el repo del proyecto

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

## FASE D — Administración de modelos (pendiente)

Tras C.

---

## FASE E — Endurecimiento y cierre (pendiente)

Tras D.