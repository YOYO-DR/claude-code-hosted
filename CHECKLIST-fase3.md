# CHECKLIST-fase3.md — confirmación manual de Yoiner

Lo automatizable + el e2e real contra MiniMax ya está verde (55 tests, ruff+mypy,
y las corridas en vivo documentadas en PROGRESS.md → Gate 3). Falta tu vistazo
manual a la UX de aprobaciones.

## Preparación
- Proyecto en modo aprobación: `epsilon` (policy `approve`, timeout 30s).
- Para forzar una aprobación, pídele al agente algo que NO sea un comando "seguro":
  crear un archivo con **Write**, o un `git push`/`rm` con **Bash**. (Los `echo`
  y lecturas triviales el CLI los auto-aprueba sin preguntar.)

## Checklist UI
- [ ] Arranca una sesión de `epsilon` y pídele: "usa Write para crear
      /srv/projects/epsilon/PRUEBA.txt con 'hola'". Aparece el banner
      **⏳ Aprobación requerida** en la sesión, y el badge rojo **Aprobaciones**
      en la cabecera se incrementa.
- [ ] **Permitir** → el archivo se crea y el banner desaparece.
- [ ] Repite y pulsa **Denegar** → el agente reporta que fue denegado; el
      archivo no se crea.
- [ ] Repite y no respondas ~30s → expira; el agente sigue (mensaje instructivo)
      y la fila pasa a `expired` en `/admin/core/permissionrequest/`.
- [ ] Página **Aprobaciones** (`/permissions/`): lista global de pendientes con
      Permitir / Denegar / Permitir siempre. Resuelve desde ahí y desaparece.
- [ ] **Permitir siempre** sobre un `git push` → la 2da vez no vuelve a
      preguntar (regla `Bash(git push *)` queda en la policy `approve` del
      admin).

## Carrera web (opcional)
- [ ] Abre la misma sesión en dos pestañas, provoca una aprobación y pulsa a la
      vez en ambas: una resuelve, la otra muestra "ya respondida".

## Nota
Tras el gate, la policy `approve` quedó con la allowlist vacía (se limpió la
regla de prueba). `epsilon` no tiene archivos de prueba.
