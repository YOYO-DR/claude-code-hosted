# CHECKLIST-fase0.md — confirmación manual de Yoiner

Lo automatizable ya está verde (ver `PROGRESS.md`). Falta tu verificación
manual de la parte de UX en navegador antes de dar el Gate 0 por cerrado:

- [ ] Abrir en el navegador
      `https://claude-code-hosted.yoyodr.dev/projects/demo/terminal`
      → pide usuario/contraseña (basicAuth). Candado de TLS válido.
      Credenciales: usuario `yoiner`, password que `install.sh` mostró una
      sola vez al generarla. Si la perdiste, se regenera borrando
      `/etc/panel/ttyd.htpasswd` y re-corriendo `install.sh`.
- [ ] Dentro del terminal: se ve un tmux con shell del usuario `agents` en
      `/srv/projects/demo`. Escribe algo, cierra la pestaña, vuelve a abrir la
      URL → la sesión sigue igual (mismo scrollback).
- [ ] (Opcional) Lanza `claude` dentro del terminal y confirma que la CLI
      arranca.

Cuando lo confirmes, marco el Gate 0 como cerrado y arranco la Fase 1
(panel Django + worker de sesión). Antes de Fase 1 haré la **DETENCIÓN**
correspondiente si aplica, aunque el remote de repo y el dominio ya están
resueltos.

## Limpieza del proyecto `demo`

`demo` es solo para validar la escotilla. Cuando quieras lo elimino:
`systemctl disable --now ttyd@demo tmux@demo` + borrar `/srv/projects/demo` +
quitar su entrada de `deploy/ttyd/ports.json` y re-render.
