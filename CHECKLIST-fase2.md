# CHECKLIST-fase2.md — confirmación manual de Yoiner

Todo lo automatizable + el E2E real contra MiniMax ya está verde (33 tests,
ruff+mypy, y las corridas en vivo documentadas en PROGRESS.md → Gate 2). Falta
tu vistazo manual a la UX del admin y al flujo de creación de proyectos.

## Credenciales
- Panel: `https://claude-code-hosted.yoyodr.dev/` — usuario `yoiner` + password + TOTP.
- Admin de config: `https://claude-code-hosted.yoyodr.dev/admin/` (mismo login).

## Checklist admin (CRUD)
- [ ] `/admin/core/modelprofile/`: al editar un perfil, el campo **auth_token**
      aparece vacío (nunca re-muestra el token). Dejarlo vacío conserva el
      actual; escribir uno nuevo lo cifra. La columna "token" indica si hay uno.
- [ ] `/admin/core/project/add/`: crear un proyecto nuevo → tras guardar, en el
      VPS existe `/srv/projects/<slug>/` (owner `agents`), con `.git`,
      `.claude/settings.json` y `.mcp.json`. El `path` se deriva del slug.
- [ ] Acción **"Archivar"** sobre un proyecto: sus sesiones se detienen, el
      status pasa a `archived`, y los archivos/datos siguen intactos.
- [ ] Editar/crear un **McpServer** o **Skill** → no hay error (re-render vía
      sudo helper). En la página de una sesión ya arrancada de ese proyecto
      aparece el banner **"⟳ Reinicio requerido"**.

## Checklist en vivo (opcional, ya verificado por mí en PROGRESS.md)
- [ ] Arrancar sesión en `alpha` y otra en `beta` → el estado/costo fluyen; el
      primer evento `system.init` reporta `MiniMax-M3` y `all-team-models`
      respectivamente.
- [ ] Pedirle al agente de `alpha` que lea `/srv/projects/beta/...` o `~/.ssh`
      → lo rechaza (y si insistes, la herramienta Read devuelve
      "denied by your permission settings").

## Nota
Los proyectos de prueba `alpha`, `beta`, `gamma` y las skills `global-notes` /
`alpha-notes` quedaron creados en el VPS como demo. Si quieres partir limpio,
archívalos o bórralos desde el admin.
