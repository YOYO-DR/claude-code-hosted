# SP12 — Cobertura total de mensajes del SDK + fix reload + render suave + settings de contexto + menú `/`

> Plan vivo. Se marca cada fase al completarse. Fuente detallada:
> `~/.claude/plans/indexed-nibbling-sutherland.md`.

## Progreso

- [x] **Parte 1 — Fix bug de recarga** (backend ✅ + test `test_worker_persist.py`)
- [x] **Parte 2 — Cobertura total SDK** (backend normalize ✅ + frontend `default`/kinds/toggle ✅)
- [x] **Parte 3 — Render suave** (fade/slide, cursor parpadeante, scroll suave, reduced-motion ✅)
- [x] **Parte 4 — Máx. contexto + umbral auto-compact por modelo** (modelo+migración+API+worker+form+barra ✅)
- [x] **Parte 5 — Menú `/` de comandos** (worker `slash_commands` + popup SPA ejecutar-al-elegir ✅)
- [x] **Deploy + E2E** (migrate 0007 aplicada, SPA build, restart; validado en vivo con Playwright ✅)

> Código completo + desplegado + validado E2E. Backend verde (240 tests), `tsc`+`vite build` verde.
> Fuente de comandos `/`: `get_server_info()` del init (no `slashCommands` del context-usage, que es solo un
> resumen de tokens). Migración `0007`.
>
> **Causa dominante del "se pierden mensajes al recargar"**: NO era solo el truncado `ui_events[0]` (eso afecta
> a modelos multi-bloque tipo Anthropic). Con MiniMax (mensajes de un bloque) el culpable principal era el
> merge de `tool_result` en `ingestEvent`, que pisaba `bubble.ui.kind="tool_result"` → BubbleView devolvía
> null → **el tool_call desaparecía al llegar su resultado** (en vivo y al recargar). Fix: conservar el ui del
> tool_call, mostrar el resultado anidado. Commit `92a6a66`.
>
> **Validación E2E en vivo** (sesión cc2b1359, MiniMax-M3):
> - Recarga: la conversación COMPLETA persiste — 2 tool calls Read (uno ERROR "Invalid pages", uno OK
>   "E2E_OK_..."), thinking, agent_text, run_result. Antes desaparecían.
> - Menú `/`: popup con comandos reales (/verify, /code-review, /deep-research, /debug…), ejecuta al elegir.
> - Barra de contexto: `22.1k/200k (11%)` + marcador `⚡84%` (umbral derivado del SDK en vivo).
> - Form de modelos: campos Máx. contexto + Umbral auto-compact visibles.
> - Bonus: arreglado el freeze latente de la barra (efímeros seq=0 ya no pasan por el dedup del componente).
>
> Commits: `8fa750d` (SP12) + `92a6a66` (fix tool_call render).
> Pendiente E2E-gated (no bloqueante): (4b) que el CLI realmente compacte en el umbral configurado; (5) qué
> built-ins ejecuta el SDK al enviarlos.

---

## Decisiones del usuario
- **Contexto/compact**: por modelo (campos en `ModelProfile`).
- **Menú `/`**: ejecutar al elegir (comandos reales del SDK `slashCommands` + `.claude/commands`).
- **Verbosidad**: limpio con toggle "mostrar detalles"; catch-all para no perder nada.

## Invariantes de seguridad
Nunca commitear secretos; token del modelo solo en env del worker; secretos MultiFernet en BD; MCPs in-process;
sudo helpers con allowlist; `redis-py async` con `socket_timeout=None`.

---

## Parte 1 — Fix reload
**Raíz** (`workers/session_worker.py:299`): un `AssistantMessage` multi-bloque normaliza a N UIEvents con el
mismo seq, pero solo se persistía `ui_events[0]` → al recargar solo sobrevive el primer bloque (thinking).
**Compañero** (`consumers.py:_fetch_backlog`): omitía `ui_event`.

Fix:
- `StreamAccumulator`: flag `produced_this_turn()` + `reset_turn()`. ✅ hecho
- `_run_turn`: `reset_turn()` al inicio del turno.
- `_emit`: persistir cada UIEvent con seq propio; suprimir re-publish live de agent_text/agent_thinking
  cuando hubo streaming (anti-duplicado); publicar el resto.
- `_fetch_backlog`: incluir `ui_event`.
- Test: macro `[thinking, text, tool_use]` → 3 filas persistidas con ui_event.

## Parte 2 — Cobertura SDK
Backend `normalize.py`: emitir kinds hoy tragados — `compact` (compact_boundary), `rate_limit`
(RateLimitEvent), `error` (AssistantMessage.error / MirrorError), `task` (task_*), server-tools
(ServerToolUse/Result con `payload.server_tool`), `hook`/`telemetry` (verbosos). Desconocido → degradar, nunca perder.
Frontend `SessionDetail.tsx`: `default:` en el switch (bubble genérico con JSON crudo), casos nuevos, toggle
"mostrar detalles" (oculta hook/telemetry/unknown). `uievents.ts`: kinds + payloads. Fix doble-set de botones en
permission_request. `styles.css`: clases nuevas.

## Parte 3 — Render suave
`styles.css`: `@keyframes bubble-in` (fade+translateY) en `.bubble`; `@keyframes blink` en el cursor `▍`;
`@media (prefers-reduced-motion: reduce)`. `SessionDetail.tsx`: scroll `behavior:'smooth'` solo si el usuario
estaba cerca del fondo (`isNearBottom()`).

## Parte 4 — Contexto/compact por modelo
`ModelProfile` (+migración): `max_context_tokens`, `auto_compact_threshold`. Exponer en API + form SPA.
`_poll_context_usage`: usar el max del modelo como denominador y recomputar %; emitir `auto_compact_threshold`
para pintar marcador en `.ctx-bar`. 4b (umbral real vía `.claude/settings.json`): verify-gated → si el proveedor
no compacta, queda display-only.

## Parte 5 — Menú `/`
Worker: emitir UIEvent efímero `slash_commands { commands }` desde `slashCommands` de `get_context_usage()`.
Frontend: popup sobre `.input-bar` al teclear `/`, filtro + navegación teclado, auto-envía al elegir.
E2E: verificar qué built-ins ejecuta el SDK; custom `.claude/commands` seguros.

## Verificación
1. Reload muestra thinking+texto+tools. 2. Cobertura: server-tool, task y kind inventado renderizan; toggle
oculta hooks. 3. Suave: fade, blink, scroll, reduced-motion. 4. Settings: barra usa max correcto + marcador.
5. `/` abre popup y ejecuta. 6. `pytest` verde + test nuevo multi-bloque.
