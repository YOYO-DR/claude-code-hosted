# PLAN — Panel Web para Sesiones de Claude Code en VPS

> **Documento para Claude Code (ejecutor).** Tienes acceso root a un VPS de prueba. Vas a construir, instalar y validar esta plataforma completa. Este documento es tu fuente de verdad: no avances de fase sin cumplir el *gate* de validación de la fase anterior. Reporta al final de cada fase qué pruebas corriste y su resultado.

---

## 0. Contexto y reglas del ejecutor

**Qué se construye:** una capa web (un solo usuario: Yoiner) sobre sesiones headless de Claude Code corriendo en este VPS. Similar a OpenHands pero sin sandbox: los agentes operan directo sobre el VPS. Stack objetivo: Django (ASGI + Channels) + PostgreSQL + Redis + Traefik, workers Python con el **Claude Agent SDK**.

**Reglas obligatorias para ti (Claude Code):**

1. **Pruebas exhaustivas, no felices.** Cada fase lista sus casos de prueba. Son el mínimo: agrega todos los casos límite que identifiques (inputs vacíos, procesos muertos, Redis caído, red intermitente, respuestas duplicadas, unicode, payloads gigantes). Un caso no probado es un caso fallido.
2. **Automatiza las pruebas.** Todo lo verificable por código va en `tests/` (pytest + pytest-asyncio + pytest-django). Lo que requiera interacción humana (Telegram, GitHub, UI), lo declaras como **checklist manual** y esperas confirmación de Yoiner antes de marcar el gate.
3. **Credenciales: siempre pídelas, nunca las inventes.** GitHub lo administra Yoiner. Cuando llegues a una integración que requiera credenciales, **detente y solicita exactamente lo que necesitas**: tipo de token (fine-grained PAT / classic / `gh auth login` / GitHub App), scopes exactos, repos sobre los que aplica, y dónde lo vas a guardar. Igual con el bot token de Telegram y el chat_id. No continúes esa parte hasta recibirlos.
4. **Todo secreto va cifrado en DB o en env del servicio systemd** (con `ProtectHome`, permisos 600). Nunca en el repo, nunca en logs, nunca en mensajes de Telegram.
5. **Idempotencia.** Scripts de instalación y migraciones deben poder correrse dos veces sin romper nada. Pruébalo corriéndolos dos veces.
6. **Registra decisiones.** Mantén `DECISIONS.md` con cada desviación del plan y su porqué.

---

## Fase 0 — Base del VPS + escotilla de terminal (tmux + ttyd + Traefik)

**Objetivo:** acceso web inmediato a sesiones de terminal, y la infraestructura base instalada. Esta fase queda como "escotilla" permanente aunque exista la UI propia.

**Tareas:**
- Instalar y configurar: Docker, Traefik (con certificados TLS vía Let's Encrypt o los que indique Yoiner), PostgreSQL, Redis, Python 3.12, Node (para Claude Code CLI), tmux, ttyd.
- Crear usuario de sistema `agents` (no root) bajo el cual correrán las sesiones.
- Un servicio ttyd por "slot" de sesión (hasta 8), cada uno adjunto a una sesión tmux nombrada (`cc-<slug>`), expuesto vía Traefik en `term-<slug>.<dominio>` con basicAuth.
- Documentar en `INFRA.md` puertos internos usados por la propia plataforma.

**Pruebas / gate Fase 0:**
- [ ] `curl` a cada ruta Traefik: 401 sin credenciales, 200 con ellas, TLS válido.
- [ ] Abrir ttyd desde navegador, lanzar `claude` dentro del tmux, cerrar navegador, reconectar: la sesión sigue viva y con historial.
- [ ] Matar el proceso ttyd → systemd lo reinicia y la sesión tmux sobrevive.
- [ ] Reboot del VPS → todo vuelve solo (enable en systemd).
- [ ] 8 sesiones tmux con `claude` idle simultáneas: medir RAM/CPU y registrar en `INFRA.md`. Si el VPS no aguanta, detente y repórtalo.

---## Fase 1 — Worker de sesión con Agent SDK + streaming a web

**Objetivo:** un proyecto hardcoded, un worker Python que corre `ClaudeSDKClient`, eventos en vivo por WebSocket, historial persistente.

**Tareas:**
- Proyecto Django `panel/` (ASGI, Channels con Redis como channel layer). Auth: Django auth + TOTP. Un solo superusuario.
- Modelos mínimos: `Project`, `Session`, `Event` (todo evento del SDK se guarda en Postgres además de publicarse en Redis `session:<id>:out`).
- Worker `session_worker.py`: proceso asyncio por sesión, systemd template `claude-session@.service`, corre como usuario `agents`. Recibe mensajes por `session:<id>:in`.
- UI mínima: chat con stream en vivo, indicador de estado (corriendo / esperando / terminado), costo acumulado del evento `result`.
- Reconexión: al abrir la página se carga historial de Postgres y se engancha al canal live sin perder ni duplicar eventos (usa secuencia/offset por evento).

**Pruebas / gate Fase 1:**
- [ ] pytest: persistencia de eventos, orden y no-duplicación en reconexión, serialización de todos los tipos de evento del SDK (init, assistant, tool_use, tool_result, result, errores).
- [ ] Enviar tarea real ("crea un archivo X, luego léelo"): verificar en disco y en la UI.
- [ ] Matar el worker a mitad de una tarea → systemd reinicia → la UI muestra el corte honestamente (no "corriendo" fantasma).
- [ ] Redis caído 30 s durante streaming: el worker se recupera, ningún evento se pierde en Postgres.
- [ ] WebSocket sin auth → rechazado. Mensaje malformado por `:in` → logueado y descartado sin tumbar el worker.
- [ ] Dos pestañas abiertas de la misma sesión: ambas reciben el stream.

---

## Fase 2 — Proyectos, configuración renderizada y perfiles de modelo

**Objetivo:** CRUD de proyectos desde la web; la DB es la fuente de verdad y un renderer escribe la config a disco donde Claude Code la lee.

**Tareas:**
- Modelos: `Skill` (global/por proyecto), `McpServer`, `ModelProfile` (Anthropic vs MiniMax: `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN` cifrado), `PermissionPolicy` (modo `auto` / `approve` + `allowedTools` + `deny`).
- Renderer idempotente que materializa: `~/.claude/CLAUDE.md` (prompt general, ver §Prompt general), `~/.claude/skills/`, y por proyecto `CLAUDE.md`, `.claude/skills/`, `.mcp.json`, `.claude/settings.json` (env del modelo + reglas de permisos).
- **Deny rules obligatorias en todo proyecto, no removibles desde la UI:** `.env*`, `~/.ssh`, `~/.claude`, `/etc`, ruta del panel, y los directorios de los demás proyectos.
- Crear proyecto = dir + git init (o clone, ver Fase 5) + render + arrancar worker. Editar MCP/skills marca la sesión como "requiere reinicio" (los cambios de MCP no recargan en caliente).
- Archivar proyecto = detener worker + conservar datos.

**Pruebas / gate Fase 2:**
- [ ] pytest del renderer: golden files (config esperada byte a byte), render doble sin diffs, escape de contenido raro en nombres/valores.
- [ ] Crear 2 proyectos con perfiles distintos (uno Anthropic, uno MiniMax): el evento `init` de cada sesión reporta el modelo correcto.
- [ ] Verificar que el agente del proyecto A **no puede** leer/escribir el directorio del proyecto B ni `~/.ssh` (pedírselo explícitamente y confirmar el deny).
- [ ] Editar un MCP en la web → UI exige reinicio → tras reiniciar, `/mcp` refleja el cambio.
- [ ] Skill global visible en ambos proyectos; skill de proyecto visible solo en el suyo.

---

## Fase 3 — Permisos mixtos (`can_use_tool`) + aprobaciones web

**Objetivo:** modo por proyecto y por tool. Lo pre-aprobado fluye; lo indeciso pregunta.

**Tareas:**
- Implementar callback `can_use_tool` en el worker: consulta `PermissionPolicy`; si requiere humano, crea `PermissionRequest` (UUID, tool, input truncado y completo), publica en `session:<id>:perm`, espera con timeout configurable (default 15 min) → al vencer: deny con mensaje instructivo al agente.
- Claim atómico de respuesta: `SET perm:<uuid>:answer <val> NX`. El segundo respondedor recibe "ya respondida".
- UI: cola de aprobaciones pendientes con Permitir / Denegar / **Permitir siempre** (escribe la regla en `allowedTools` del proyecto y re-renderiza).
- El callback puede **reescribir input** (gancho para Fase 4 puertos).

**Pruebas / gate Fase 3:**
- [ ] pytest: policy en `auto` no genera requests para tools permitidas; en `approve` sí; deny rules cortan sin pasar por el callback; timeout produce deny y desbloquea el worker; claim atómico con dos respuestas concurrentes (una gana, otra recibe conflicto).
- [ ] E2E: proyecto en `approve`, pedir `git push` → aparece en la web → aprobar → se ejecuta. Repetir denegando → el agente recibe el deny y continúa sin colgarse.
- [ ] "Permitir siempre" sobre `Bash(docker ps:*)` → segunda invocación ya no pregunta.
- [ ] Reinicio del worker con una request pendiente: la request expira o se reancla, jamás queda zombie aprobable.

---

## Fase 4 — Bot de Telegram (uno solo, supergrupo con topics) + MCP de puertos

**Objetivo:** aprobar desde Telegram; coordinación de puertos entre agentes como restricción real, no convención.

### 4a. Telegram
- **Un solo bot**, un supergrupo con **un topic por proyecto** (crear topic al crear proyecto; guardar `message_thread_id`).
- ➡️ **DETENTE Y SOLICITA a Yoiner:** el bot token (de @BotFather), y que agregue el bot al supergrupo como admin con permiso de manage topics; luego captura su `chat_id` y el user_id de Yoiner para la allowlist.
- Webhook (no polling): `setWebhook` a `https://<panel>/tg/webhook` con `secret_token`; validar header `X-Telegram-Bot-Api-Secret-Token`; descartar updates de cualquier user_id fuera de la allowlist.
- Suscriptor de `session:*:perm`: publica cada request en el topic del proyecto con **inline keyboard** (Permitir / Denegar / Permitir siempre; `callback_data` = UUID, ≤64 bytes). Texto: proyecto + tool + input truncado (límite 4096 chars).
- Tras resolución (desde web, Telegram o timeout): **editar el mensaje** con el desenlace y quitar los botones. Mensajes de texto libres sin request pendiente → ignorar (responder nada o un "sin solicitudes pendientes" silencioso, a criterio).

### 4b. MCP de puertos
- MCP server propio (stdio, montado global en todos los proyectos): `allocate_port(project, purpose)`, `list_ports()`, `release_port(port)` sobre tabla `PortRegistry` en Postgres, con rango configurable y exclusión de puertos de la plataforma.
- Gancho en `can_use_tool`: si un comando incluye un bind a puerto ya registrado por otro proyecto, reescribir al puerto asignado o denegar con instrucción de usar `allocate_port`.

**Pruebas / gate Fase 4:**
- [ ] Unit: firma de webhook inválida → 403; user fuera de allowlist → ignorado; `callback_query` duplicado (doble tap) → segunda pulsación recibe "ya respondida"; edición del mensaje tras timeout.
- [ ] E2E con Yoiner (checklist manual): aprobar desde Telegram y ver ejecutar; denegar; carrera web-vs-Telegram; escribir texto suelto sin pending → no pasa nada; request en topic correcto.
- [ ] MCP puertos: dos proyectos piden puerto "3000" → obtienen distintos; `list_ports` consistente; caída de Postgres durante allocate → error limpio al agente, sin puerto fantasma; liberar y reasignar.
- [ ] E2E: pedir a dos agentes levantar un servidor "en el 8080" a la vez → cero colisiones reales (verificar con `ss -tlnp`).

---

## Fase 5 — GitHub (MCP para el agente, API para la plataforma)

**Objetivo:** crear proyectos desde repos, y que los agentes trabajen con issues/PRs. Separación estricta: el **agente** usa GitHub MCP; la **plataforma** usa la API REST (clonar, crear repos, webhooks).

- ➡️ **DETENTE Y SOLICITA a Yoiner, especificando exactamente:** (1) para la plataforma: fine-grained PAT con los repos exactos y permisos `contents:rw`, `metadata:r`, `pull_requests:rw` — pedir confirmación de la lista de repos; (2) para el MCP de los agentes: si prefiere el mismo PAT, uno separado con menos permisos (recomendado: **sin** merge), o `gh auth login` en el VPS. Presenta las opciones con pros/contras y espera su decisión. No asumas scopes: enuméralos y que él confirme.
- Crear proyecto desde repo: clone vía la API/token de plataforma, branch de trabajo por sesión (valorar `git worktree` por sesión), render de config, worker arriba.
- Config del GitHub MCP inyectada por el renderer solo en proyectos que lo activen.

**Pruebas / gate Fase 5:**
- [ ] Clonar un repo de prueba (pedir a Yoiner cuál usar), crear rama, que el agente haga un cambio y abra PR vía MCP → verificar PR real.
- [ ] Confirmar que el token del agente **no puede** mergear (intento de merge → 403) ni tocar repos fuera de la lista.
- [ ] Token inválido/revocado → error claro en UI, no crash. Rate limit simulado → backoff.
- [ ] PAT jamás aparece en logs, eventos guardados, ni mensajes de Telegram (grep exhaustivo).

---

## Fase 6 — Endurecimiento y validación final

- Backups: dump diario de Postgres + `~/.claude` + configs de proyectos (script + timer systemd; probar un restore real en un dir limpio).
- Logrotate de todos los servicios; límites de recursos por worker (`MemoryMax` en systemd); alerta simple (Telegram, topic "sistema") si un worker crashea en loop.
- **Suite completa de regresión** de todas las fases en verde + checklist manual final con Yoiner.
- Simulacro de caos: reboot en frío con 3 sesiones a mitad de tarea → todo el estado es honesto al volver; llenar el disco al 95% → los workers fallan limpio; matar Redis y Postgres por separado bajo carga.
- Informe final: `REPORT.md` con arquitectura real construida, cobertura de pruebas, métricas de recursos con 8 sesiones, deudas conocidas.

---

## Prompt general (contenido inicial de `~/.claude/CLAUDE.md`)

El renderer lo genera desde la DB; este es el contenido de arranque (editable luego desde la web):

```markdown
# Reglas globales para todos los agentes de este VPS

- Compartes este VPS con otros agentes trabajando en otros proyectos. Tienes acceso amplio: úsalo con criterio.
- PUERTOS: nunca elijas un puerto a mano. Usa el MCP `ports`: `allocate_port` antes de exponer cualquier servicio, `list_ports` para consultar, `release_port` al desmontar. Si un puerto que esperabas está ocupado, es de otro agente: pide otro, no mates procesos ajenos.
- Nunca detengas, reinicies ni modifiques contenedores, servicios o procesos que no pertenezcan a tu proyecto. En duda, pregunta (genera una solicitud de aprobación).
- Trabaja solo dentro del directorio de tu proyecto. Los directorios de otros proyectos, `~/.ssh`, `~/.claude` y `/etc` están denegados: no intentes rodear el deny.
- Nombres de contenedores, redes y volúmenes Docker: prefíjalos con el slug de tu proyecto.
- Si una solicitud de aprobación expira sin respuesta humana, continúa con tareas que no la requieran o deja el trabajo en un estado limpio y documentado.
- Registra en el archivo `NOTES.md` de tu proyecto cualquier recurso compartido que uses y cualquier estado que otro agente deba conocer.
- Git: trabaja en ramas, abre PRs. Nunca push a main/master sin aprobación explícita.
```

---

## Orden de ejecución y criterio de avance

Fase 0 → 1 → 2 → 3 → 4 → 5 → 6, estrictamente. El gate de cada fase = **todas** sus pruebas automatizadas en verde + checklist manual confirmada por Yoiner. Si un supuesto del plan resulta falso en la práctica (API del SDK distinta, límite del VPS, cambio en Claude Code), no improvises en silencio: documenta en `DECISIONS.md`, propone el ajuste y continúa solo si no compromete seguridad ni los gates.