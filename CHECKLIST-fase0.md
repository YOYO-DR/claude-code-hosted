# CHECKLIST-fase0.md — confirmación manual de Yoiner

**Gate 0 cerrado 2026-07-19** (validado por Yoiner en navegador).

Lo automatizable ya está verde (ver `PROGRESS.md`).

## Validación manual realizada

- [x] Abrir en el navegador
      `https://claude-code-hosted.yoyodr.dev/projects/demo/terminal`
      → pide usuario/contraseña (basicAuth). Candado de TLS válido.
- [x] Dentro del terminal: tmux con shell del usuario `agents` en
      `/srv/projects/demo`. Cierre de pestaña + reapertura → scrollback
      intacto.
- [x] `claude` CLI arranca dentro del terminal.

## Nota sobre las credenciales (descubierto al rotar)

El basicAuth que ve el navegador **lo sirve Traefik**, no ttyd. El archivo
real es `/etc/traefik/secrets/ttyd.htpasswd` (middleware `ttyd-auth` en
`deploy/traefik/dynamic/middlewares.yml`). `/etc/panel/ttyd.htpasswd` existe
pero hoy nadie lo lee.

Para rotar la contraseña hay que regenerar el de Traefik y reiniciar el
contenedor Traefik. Credenciales actuales en `vps.env` como
`TRAEFIK_USER` / `TRAEFIK_PASS` (las `TERMINAL_DEMO_*` son legado).

## Limpieza del proyecto `demo`

`demo` es solo para validar la escotilla. Cuando quieras lo elimino:
`systemctl disable --now ttyd@demo tmux@demo` + borrar `/srv/projects/demo` +
quitar su entrada de `deploy/ttyd/ports.json` y re-render.
