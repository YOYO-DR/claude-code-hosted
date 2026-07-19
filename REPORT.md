# REPORT.md — Panel Web para Sesiones de Claude Code en VPS

Estado: **Fases 0–6 cerradas y verificadas en vivo** contra el VPS
`169.58.33.122` (dominio `claude-code-hosted.yoyodr.dev`, proxied por Cloudflare)
usando **MiniMax-M3** vía proxy litellm como modelo. 95 tests unit/integración
verdes; ruff + mypy limpios.

---

## 1. Arquitectura final

```
Internet ──TLS(Cloudflare Full strict)──> Traefik (host net, 80/443)
                                              │  ├─ /  ............ panel (uvicorn ASGI :8000)
                                              │  └─ /projects/<slug>/terminal ... ttyd@<slug> (escotilla)
                                              │
  panel (Django ASGI + Channels)  usuario `panel`
    ├─ UI: sesiones, stream WS, cola de aprobaciones, /github, /admin
    ├─ webhook Telegram (/tg/webhook)
    └─ orquesta workers vía sudo helpers + systemctl
                                              │
  worker de sesión  claude-session@<sid>  usuario `agents`, MemoryMax=1G
    ├─ Claude Agent SDK (ClaudeSDKClient) → CLI bundled → MiniMax-M3
    ├─ MCP in-process: ports (§4.5) y github (§5.3) — sin secretos a disco
    ├─ can_use_tool: permisos mixtos + hook de puertos
    └─ bus Redis (BRPOP :in, PUBLISH :out/:perm, heartbeat)

  tg-bridge  (psubscribe session:*:perm → Telegram; perm:resolved → edita)
  monitor.timer (disco/crash-loop/heartbeat → alerta topic sistema)
  backup.timer  (pg_dump + .claude → tar cifrado → S3/MinIO)

  Infra (Docker, panel-infra.service): Traefik, Postgres 16, Redis 7 (AOF)
    Postgres/Redis SOLO en 127.0.0.1 (nunca expuestos).
```

**Fuente de verdad:** la DB. El `renderer` materializa config (CLAUDE.md,
`.claude/settings.json`, `.mcp.json`, skills) a disco; nadie los edita a mano.

---

## 2. Fases y gates (todos verdes)

| Fase | Entregable | Verificación en vivo |
|------|-----------|----------------------|
| 0 | Infra base + escotilla ttyd | TLS válido, tmux sobrevive a kill/reboot, PG/Redis no expuestos |
| 1 | Panel Django + worker | E2E MiniMax: crea/lee archivo; kill-9 → revive; Redis 30s → recupera |
| 2 | Renderer + CRUD + provisioning | golden files, deny duro (`denied by your permission settings`), skills por scope, provisioning como `panel` vía sudo |
| 3 | Permisos mixtos + aprobaciones web | approve/deny/timeout/allow_always e2e; carrera de threads; rewrite; expire al reiniciar |
| 4 | Telegram + MCP de puertos | 80 allocate concurrentes 0 dups; 2 agentes sin colisión; aprobar por botón de Telegram (`resolved_by=telegram`) |
| 5 | GitHub | PR #1 real vía MCP; token cifrado en BD, no filtra (grep=0); sin tool de merge |
| 6 | Endurecimiento | backup+restore real; alertas a sistema; caos (Redis/PG/disco/reboot/reloj) |

---

## 3. Cobertura

- **95 tests** (unit + integración): stream/seq, serialización, crypto, eventos,
  renderer (golden byte-a-byte), permisos (carrera real, timeout, rewrite,
  monotonic), puertos (constraint anti-dup, guard), telegram (webhook, límites,
  callback_data, recreación de topic), github (token cifrado, errores 401/403/429,
  extraHeader oculta token, MCP sin merge), monitor (cooldown, crashed, disco).
- **E2E en vivo** (documentados en `PROGRESS.md`): un flujo real por gate contra
  MiniMax-M3 y GitHub.

---

## 4. Métricas (8 sesiones activas simultáneas)

VPS: 8 GB RAM, 4 vCPU, disco 96 GB.

| | Valor |
|--|-------|
| RAM usada con 8 workers idle | **~2.36 GB** (de 7.9 GB) |
| RAM disponible | **~5.5 GB** |
| Marginal por sesión (worker python + CLI) | ~175 MB efectivos (RSS bruto ~330 MB, mucho compartido) |
| Cap por worker | `MemoryMax=1G` (nunca alcanzado en idle) |
| Swap | 0 (no necesario) |

Holgura amplia para 8 sesiones; el límite práctico ronda 15–20 sesiones idle
antes de presionar RAM. Los puertos de servicios de agentes viven en 20000–29999.

---

## 5. Modelo de seguridad

- **Secretos nunca a disco de proyecto:** token del modelo (env en memoria del
  worker), token de GitHub (cifrado en Config, en memoria; git vía `extraHeader`,
  nunca en `.git/config`), DB/Redis solo en `127.0.0.1`. MCP de puertos y GitHub
  **in-process** justamente para no volcar creds al `.mcp.json` legible por el
  agente (D9/D10).
- **Deny duro** (constante en código, siempre inyectada): otros proyectos,
  `~/.ssh`, `~/.claude`, `/etc`, `/opt/panel`. Verificado que corta ANTES del
  callback.
- **Privilegios:** el panel corre sin privilegios; delega chown/clone/render en
  helpers root vía `sudo -n` con sudoers restringido (paths validados).
- **Cifrado en reposo:** MultiFernet (rotación sin migración). Backups AES-256.
- **Telegram:** webhook con secret token + allowlist de user_id; solo callback_query.
- **GitHub:** el agente no puede mergear (sin tool) ni tocar otros repos (MCP
  ligado a su repo). Grep exhaustivo: el PAT no aparece en logs/eventos/repo.

---

## 6. Deudas conocidas

1. **Ruido de `brpop`**: el worker loguea `Timeout reading from 127.0.0.1:6379`
   en polls idle (socket timeout < bloqueo de 5s). No afecta la entrega; conviene
   afinar el health-check/socket del cliente Redis.
2. **CLAUDE.md de repos clonados**: el renderer sobrescribe el `CLAUDE.md` del
   repo con el del proyecto (DB). `.claude/` y `.mcp.json` sí se excluyen del git.
   Si un repo trae su propio CLAUDE.md, se pierde a favor del de plataforma.
3. **"Sin merge" = omisión de tool**, no scope del token (Yoiner eligió el mismo
   PAT). Candado duro real: **branch protection** en el repo.
4. **ttyd (escotilla) sin MCP de puertos/GitHub**: es uso manual del operador,
   fuera del alcance de la coordinación automática.
5. **`git push` del agente** usa `extraHeader` en argv (visible en `/proc` al
   mismo usuario `agents` momentáneamente). El clone ya evita esto (token por
   STDIN). Mitigable con un askpass por env si se endurece el modelo multi-agente.
6. **S3/MinIO**: ✅ verificado en vivo (subida + round-trip + retención 14)
   sobre `s3://claude-code-hosted/panel/` (endpoint MinIO vía sslip.io).
7. **`uv sync` no instaló boto3** (quirk lock/sync); se usó `uv pip install` y se
   regeneró `uv.lock`. En un install limpio, `uv sync` desde el lock actualizado
   debería bastar.

---

## 7. Runbook de operación

**Desplegar / actualizar:**
```bash
runuser -u panel -- env HOME=/home/panel git -C /opt/panel pull --ff-only
sudo bash /opt/panel/deploy/link-units.sh   # units + migrate + collectstatic + timers
```

**Arrancar / parar una sesión:** desde el panel (botón ▶ / ■). Por debajo:
`sudo systemctl start|stop claude-session@<sid>.service` (solo el user `panel`).

**Servicios:** `panel.service`, `tg-bridge.service`, `panel-infra.service`
(Docker), timers `backup.timer` (03:30) y `monitor.timer` (cada 2 min).

**Backup manual:** `sudo /opt/panel/deploy/backup.sh`
(→ `/var/backups/panel/panel-*.tar.enc`, cifrado, + subida S3 si configurada).

**Restaurar un backup (a DB de prueba, no pisa prod):**
```bash
sudo /opt/panel/deploy/restore.sh /var/backups/panel/panel-YYYYmmdd-HHMMSS.tar.enc
# → restaura en DB 'panel_restore_test'; verifícala y, si procede, promuévela.
```
La passphrase de cifrado está en `/etc/panel/backup.pass` (600 root) —
**respáldala aparte**: sin ella los backups no se descifran.

**Rotar tokens:**
- Modelo: `/admin/` → ModelProfile → campo `auth_token` (write-only, se cifra).
- GitHub: página `/github/` → pegar nuevo PAT (valida + guarda cifrado).
- Telegram: `PANEL_TELEGRAM_BOT_TOKEN` en `/etc/panel/panel.env` + reiniciar
  `panel` y `tg-bridge`; re-registrar webhook con `manage.py tg_setup`.
- Fernet (secretos en BD): añadir clave nueva al frente de
  `PANEL_SECRET_ENC_KEYS` (MultiFernet descifra con todas, cifra con la 1ª).

**Alertas:** llegan al topic **sistema** del grupo de Telegram (disco >90%,
worker en crash-loop, sesión sin heartbeat). Cooldown 30 min por condición.

**Caos / pruebas de resiliencia:** `scripts/chaos/` (redis_outage, pg_outage,
disk_fill, cold_reboot).
