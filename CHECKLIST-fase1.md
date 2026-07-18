# CHECKLIST-fase1.md — confirmación manual de Yoiner

Lo automatizable ya está verde (20 tests, ruff+mypy limpios, WS 4401
verificado en vivo, E2E del bus y persistencia). Falta tu verificación
manual de la UX y el arranque real de un worker con el Agent SDK (gate 1.5).

## Antes de probar (recordatorios)

- Credenciales:
  - Usuario: `yoiner`
  - Contraseña del panel: la que provisionaste al crear el superusuario.
  - TOTP: escanea el `otpauth://` que devolvió `setup_totp` (si necesitas
    uno nuevo: `sudo -E python /opt/panel/manage.py setup_totp yoiner`).
- Token Anthropic: **no hay todavía**. Hasta que no se agregue un token
  válido al `ModelProfile anthropic-default`, los workers no pueden llamar
  a la API y el E2E con el SDK no funciona.

## Checklist

- [ ] Login en `https://claude-code-hosted.yoyodr.dev/login/` (usuario +
      contraseña + TOTP) → redirige a la lista de sesiones.
- [ ] Click ▶ Demo → arranca el worker (`claude-session@<sid>`). En este
      punto el worker muere porque el ModelProfile no tiene token — eso es
      esperado, lo importante es que la fila de Session se crea y el
      status pasa a `crashed` honestamente (no "running" fantasma).
- [ ] Recargar la página de la sesión: aparece el registro de "crashed",
      no eventos huérfanos.
- [ ] La ruta ttyd `/projects/demo/terminal` sigue funcionando (basicAuth,
      independiente del panel).

## Para continuar con el gate 1.5 (E2E real con el SDK)

Necesito tu decisión sobre qué token usar:

**Opción A** — Tu token personal de Anthropic:
- Me lo pasas (o lo añades tú mismo vía `manage.py shell` con
  `crypto.encrypt(token)`). Lo descifra el worker en memoria y nunca toca
  disco. Con eso pruebo end-to-end: tarea real "crea archivo X y léelo",
  kill -9 al worker durante la tarea → restart → status honesto, Redis
  caído 30s → recuperación.

**Opción B** — Mock server local:
- Levanto un mock de la API de Anthropic (captura qué `base_url` golpeó el
  worker y devuelve respuestas sintéticas). Útil si no quieres exponer un
  token real todavía, pero **no cubre el flujo real con el SDK** — el gate
  pediría re-correr con token real después.

**Opción C** — Saltar el gate 1.5 ahora, seguir a Fase 2 (CRUD +
renderer) y volver a 1.5 cuando tengas token.

¿Cuál preferís?
