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

### D5 — TLS: Cloudflare Origin CA en vez de Let's Encrypt (dominio proxied)

El dominio `claude-code-hosted.yoyodr.dev` está **proxied por Cloudflare**
(nube naranja): `dig` devuelve IPs de Cloudflare (104.21.x / 172.67.x), no la
del VPS. Consecuencias:

- El browser ya recibe TLS válido del **edge de Cloudflare** (cert de Google
  Trust Services para `*.yoyodr.dev`). No hace falta emitir nada para el
  tramo browser→CF.
- El tramo **CF→origen** con Cloudflare en modo *Full (strict)* exige que el
  origen presente un cert que CF confíe. Con solo el `TRAEFIK DEFAULT CERT`
  autofirmado, CF devuelve **HTTP 526**.
- Let's Encrypt HTTP-01 **no aplica**: LE conecta contra las IPs de
  Cloudflare, no contra el origen, y CF intercepta 80/443.

Solución adoptada (decisión de Yoiner): **Cloudflare Origin CA certificate**.
Se genera vía API (`POST /certificates`, `request_type: origin-rsa`,
`requested_validity: 5475` = 15 años) usando un API token de Cloudflare con
permiso `Zone → SSL and Certificates → Edit` sobre la zona `yoyodr.dev`. La
private key se genera en el VPS (`/etc/panel/origin/key.pem`, nunca sale de
ahí); solo el CSR viaja a CF y vuelve firmado. El cert queda en
`/etc/panel/origin/cert.pem`.

Traefik lo sirve como **default certificate** vía file provider
(`deploy/traefik/dynamic/tls.yml`), montando `/etc/panel/origin` en el
contenedor. Se elimina toda la config ACME/Let's Encrypt del compose
(`--certificatesresolvers.le.*`, el volumen `traefik_certs`, el `env_file` de
`LE_EMAIL`) y `render_routes.py` usa `tls: {}` (cert default) en vez de
`certResolver: le`.

Cero renovaciones durante 15 años. Cloudflare queda en Full (strict).

Esto reemplaza por completo la pregunta original wildcard-vs-HTTP-01 (D3) y la
idea de `LE_EMAIL` en `install.sh`.

---

## D6 — Sintaxis de patrones de permisos del renderer (Fase 2)

`settings.json` usa el esquema oficial de Claude Code:
`{"permissions": {"allow": [...], "deny": [...]}}`. Los patrones siguen la
sintaxis `Tool(specifier)` con rutas estilo gitignore: `//abs/path/**` para
absolutas, `./rel` para relativas al cwd del proyecto, `**` recursivo. Las
`MANDATORY_DENY` (constante en código) ya usaban esta forma y se validó que
coincide con la doc; no hubo que ajustarlas.

El **modo** de permisos (auto→`bypassPermissions`, approve→`default`) NO va en
`settings.json`: lo fija el worker en `ClaudeAgentOptions.permission_mode`
(§4.2), coherente con "settings.json SIN env de modelo" (§4.3).

El **env del modelo** (tokens) nunca se materializa a disco: se inyecta desde
la DB en memoria del worker (§4.2/§4.3). El renderer solo escribe permisos,
skills y `.mcp.json`.

Badge "reinicio requerido": se computa comparando `updated_at` (auto_now) de
`McpServer`/`ModelProfile` del proyecto contra `session.started_at` — cero
campos ni migraciones nuevas.

---

## D7 — Modelo de privilegios del render/provisioning (Fase 2)

El panel corre como usuario `panel` (mínimo privilegio). Pero materializar
config y provisionar proyectos necesita root:
- **Leer `/etc/panel/panel.env`** (creds de DB) — 640 root:panel; el render lo
  necesita para conectar a Postgres. Sourcearlo requiere root.
- **`chown` del dir del proyecto a `agents`** — el worker (User=agents) escribe
  código ahí; `panel` no puede chownear a otro usuario.
- **Escribir config en dirs de `agents`** — root puede; `panel` no.

Solución (mismo patrón que `supervisor.py` con systemctl): dos helpers root
(`deploy/panel-render.sh`, `deploy/panel-provision.sh <slug> <path>`) invocados
por el panel vía `sudo -n`, con sudoers restringido (`sudoers.d-panel`). El
provision valida que el path esté bajo `/srv/projects` y el slug sea
`[a-z0-9-]`. `panel-provision.sh` hace mkdir + git init + chown agents + render.

`privileged.py` decide: root → render en proceso; `panel`+sudo+helper → sudo;
local/tests (sin helper) → en proceso sin chown. Los archivos de config quedan
root-owned pero world-readable (644): el agente los LEE, no los escribe. El dir
del proyecto queda `agents`-owned para que el worker escriba código.

Verificado en el VPS: deny duro de settings.json bloquea Read a
`/srv/projects/<otro>` y `~/.ssh` con
`<tool_use_error>File is in a directory that is denied by your permission
settings.</tool_use_error>`, incluso bajo `bypassPermissions` (deny > allow).
