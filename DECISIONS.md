# DECISIONS.md

Desviaciones y resoluciones de ambigüedad respecto a PLAN.md, con su porqué.

## Fase 0

### D1 — Traefik en Docker con `network_mode: host`

PLAN.md fija Traefik en Docker y Postgres/Redis también en Docker, pero Django
(usuario `panel`) y los workers (usuario `agents`) corren nativos vía systemd
(necesitan invocar `systemctl`, git, npm, docker CLI, etc. directo sobre el
host — no tiene sentido meterlos en un contenedor). Eso implica que Traefik
(en Docker) tiene que enrutar hacia procesos nativos del host (ttyd, panel
Django). En Linux, `host.docker.internal` no existe por defecto sin
`extra_hosts: host-gateway`, así que la opción más simple y estándar es correr
el contenedor de Traefik con `network_mode: host`: bindea 80/443 directo en
el host y llega a `127.0.0.1:<puerto>` de cualquier servicio nativo sin
configuración extra.

**Por qué:** menos piezas móviles que `extra_hosts` + red bridge, y evita
duplicar el mapeo de puertos.

### D2 — "Postgres/Redis no expuestos fuera de la red Docker" = bind a 127.0.0.1, no a 0.0.0.0

Interpretación literal (contenedores sin ningún puerto publicado) es
incompatible con que Django/workers nativos necesiten conectarse a ellos.
Se publican puertos de Postgres/Redis únicamente en `127.0.0.1:<puerto>`
(nunca `0.0.0.0`), de forma que sean alcanzables solo desde el propio host
y nunca desde la red pública. El gate 0 (`ss -tlnp`) valida que no aparezcan
en `0.0.0.0` ni en la IP pública.

### D3 — Un solo dominio con ruteo por path, no subdominio por proyecto

Decisión explícita de Yoiner: en vez de `term-<slug>.<dom>` (subdominio por
proyecto, como dice PLAN.md §1), todo vive bajo un único host
`claude-code-hosted.yoyodr.dev` y el proyecto se distingue por path:

```
https://claude-code-hosted.yoyodr.dev/              → panel (UI + API)
https://claude-code-hosted.yoyodr.dev/projects/<slug>/terminal → ttyd del proyecto
https://claude-code-hosted.yoyodr.dev/tg/webhook     → webhook de Telegram (Fase 4, ya era path-based en el plan)
```

Esto además hace consistente el patrón: el webhook de Telegram ya era
path-based en PLAN.md §4.6; ahora ttyd sigue el mismo esquema en vez de ser
el único caso subdominio-based.

**Consecuencia en TLS:** con un solo hostname no hace falta wildcard ni
DNS-01/token de Cloudflare — un certificado HTTP-01 normal para
`claude-code-hosted.yoyodr.dev` alcanza para todo. Se descarta la pregunta
original de wildcard vs HTTP-01: ya no aplica.

**Consecuencia en ttyd:** cada instancia corre con `--base-path
/projects/<slug>/terminal` (soportado nativamente por ttyd) y Traefik enruta
por `PathPrefix` en vez de `Host`. El router se agrega/quita dinámicamente
(archivo de config dinámica que Traefik vigila) cuando se crea/archiva un
proyecto — encaja con el renderer de Fase 2, que ahora también materializa
esta pieza.

### D4 — Pool fijo de puertos ttyd (Fase 0, previo al MCP de puertos de Fase 4)

Fase 0 no tiene todavía ni `supervisor.py` (Fase 1) ni el `PortRegistry` /
MCP de puertos (Fase 4) — ese MCP es para puertos que los propios AGENTES
abren para sus servicios, no para infraestructura de la plataforma. Para
ttyd (interno, un puerto por slot) se reserva un rango fijo
`127.0.0.1:7681-7688` (8 slots, igual al límite de sesiones concurrentes del
plan) documentado en `INFRA.md`, con una asignación simple slug→puerto en
`/opt/panel/deploy/ttyd/ports.json` que el `ExecStartPre` de
`ttyd@.service` resuelve. Revisar si esto necesita algo más sofisticado en
Fase 2 cuando exista CRUD real de proyectos.

### D5 — Email de Let's Encrypt: pedido por `install.sh`, no hardcodeado

`install.sh` exige la variable `LE_EMAIL` (prompt interactivo si no está en
el entorno) en vez de tener el email escrito en el script o en el repo.
Valor a usar en este VPS: `yoiner3216988182@gmail.com`.
