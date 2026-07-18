# PROGRESS.md

Registro de avance por fase (pruebas corridas y resultados). Ver `PLAN.md`
para el detalle de cada gate.

## Fase 0 — Infra base + escotilla ttyd

Estado: **gate automatizado verde**; pendiente confirmación manual de Yoiner
(ver `CHECKLIST-fase0.md`).

VPS: Ubuntu 24.04.4, 4 vCPU / 7.94 GB / 96 GB. `169.58.33.122`. Dominio
`claude-code-hosted.yoyodr.dev` (Cloudflare proxied, Full strict).

### Desviaciones respecto al plan (detalle en `DECISIONS.md`)

- **D1/D2:** Traefik en Docker con `network_mode: host`; Postgres/Redis en
  Docker pero publicados solo en `127.0.0.1`.
- **D3:** ruteo por path (`/projects/<slug>/terminal`) bajo un único host, en
  vez de subdominio `term-<slug>.<dom>` por proyecto.
- **D4:** pool fijo de puertos ttyd `7681-7688` (el MCP de puertos de Fase 4
  es para servicios de agentes, no para infra).
- **D5:** TLS vía Cloudflare Origin CA cert (15 años), no Let's Encrypt (el
  dominio está proxied; HTTP-01 no aplica).
- **Extra:** tmux desacoplado de ttyd en `tmux@.service` propio, para que la
  sesión sobreviva a kill/restart de ttyd (con una sola unidad, el `Restart`
  de systemd mataba el cgroup entero).

### Resultados del Gate 0 (2026-07-18)

| Check | Resultado |
|-------|-----------|
| TLS válido extremo a extremo (browser→CF→origen) | ✅ CF edge cert + Origin CA en origen; CF pasó de HTTP 526 a 200 |
| 401 sin credenciales / 200 con ellas (por router) | ✅ `/projects/demo/terminal`: 401 sin auth, 200 con `yoiner:…` |
| Sesión tmux sobrevive kill de ttyd (systemd revive) | ✅ `kill -9` a ttyd → nuevo PID; sesión + scrollback (marcador) intactos |
| Sesión tmux sobrevive cierre de navegador | ✅ Cubierto por el desacople tmux/ttyd (cerrar solo corta el websocket) |
| Reboot del VPS → todo vuelve solo | ✅ Tras reboot: infra Docker, `tmux@demo`, `ttyd@demo` activos; 401/200 OK |
| 8 sesiones `claude` idle → RAM/CPU | ✅ 9 idle = ~1.73 GB usados, ~6.2 GB libres, load ~0. Ver `INFRA.md` |
| `install.sh` 2ª corrida sin cambios destructivos | ✅ rc=0, secretos intactos (md5 OK), sin regeneración |
| `ss -tlnp`: PG/Redis no expuestos | ✅ solo `127.0.0.1`; desde el exterior 5432/6379 cerrados, 22/443 abiertos |

### Artefactos

- `deploy/install.sh` — instalación idempotente (paquetes, Docker, Node+CLI,
  uv, usuarios, ufw, secretos, enmascara ttyd.service del apt).
- `deploy/compose.infra.yml` + `deploy/systemd/panel-infra.service` — infra.
- `deploy/traefik/` — config estática + dinámica (middlewares, tls, routers
  de proyecto generados por `render_routes.py`).
- `deploy/systemd/{tmux@,ttyd@}.service` + `deploy/ttyd/*` — escotilla.
- `deploy/link-units.sh` — simlinkea unidades + habilita infra.

### Cómo operar (Fase 0)

```bash
# En el VPS, /opt/panel es el checkout del repo (deploy key de solo lectura).
sudo bash /opt/panel/deploy/install.sh        # idempotente
sudo bash /opt/panel/deploy/link-units.sh     # unidades + infra up
sudo systemctl start ttyd@<slug>              # levanta tmux@<slug> + ttyd
# terminal: https://claude-code-hosted.yoyodr.dev/projects/<slug>/terminal
```
