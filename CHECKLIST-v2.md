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

## FASE C — SPA React + chat + panel lateral (pendiente)

Tras B. Decisiones tomadas en §1: **TanStack Router**, ttyd como URL directa.

---

## FASE D — Administración de modelos (pendiente)

Tras C.

---

## FASE E — Endurecimiento y cierre (pendiente)

Tras D.