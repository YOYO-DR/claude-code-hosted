# CHECKLIST-fase4.md — confirmación manual de Yoiner

Lo automatizable + los e2e en vivo ya están verdes (79 tests, ruff+mypy; ver
PROGRESS.md → Gate 4). Esto es lo que conviene que confirmes tú en Telegram.

## Ya verificado en vivo (por mí)
- Puertos: 80 allocate concurrentes → 0 duplicados; dos agentes pidiendo un
  servidor a la vez → puertos distintos, ambos escuchando en `ss` sin colisión.
- Telegram: solicitud → mensaje con botones en el topic del proyecto; aprobar
  desde Telegram → el agente ejecuta y `resolved_by=telegram`; secret inválido
  → 403; resolución → mensaje editado sin teclado. **Tú tocaste "Permitir" y
  creó BOTON.txt.**

## Checklist manual (opcional, para tu tranquilidad)
- [ ] En el grupo, provoca una aprobación (arranca una sesión de `epsilon` desde
      el panel y pídele crear un archivo con Write). Aparece un mensaje en el
      topic **Epsilon** con [Permitir | Denegar | Permitir siempre].
- [ ] **Denegar** → el mensaje pasa a "⛔ Denegado" y el agente reporta el bloqueo.
- [ ] **Permitir siempre** sobre un `git push` → la 2da vez no pregunta.
- [ ] **Carrera web vs Telegram**: abre la sesión en el panel y el mensaje en
      Telegram; resuelve en uno → el otro queda sin efecto ("ya respondida").
- [ ] Escribe un texto suelto en el grupo (no un botón) → el bot lo ignora.
- [ ] Crea un proyecto nuevo desde el admin → se crea su topic en el grupo.

## Notas
- El bot es `@agentes_claude_code_bot`; grupo `Yoiner and Agentes Claude Code`.
- El topic "sistema" recibe alertas de proyectos sin topic propio.
- Si borras un topic a mano, el bridge lo recrea al llegar la siguiente solicitud.
- Tras el gate: epsilon con timeout 30s de nuevo, sin archivos ni puertos de prueba.
