# INFRA.md

VPS: Ubuntu 24.04.4 LTS, 4 vCPU, 7.8 GiB RAM, 96 GB disco (~90 GB libres al
arranque de Fase 0). IP: `169.58.33.122`. Dominio:
`claude-code-hosted.yoyodr.dev` (Cloudflare, **proxied**, modo Full strict).

## TLS

- Browser → Cloudflare: cert del edge de Cloudflare (Google Trust Services,
  `*.yoyodr.dev`). Gestionado por Cloudflare, nada que hacer.
- Cloudflare → origen: **Cloudflare Origin CA cert** (15 años, expira
  2041-07-14) en `/etc/panel/origin/{cert,key}.pem`, servido por Traefik como
  default cert. Ver `DECISIONS.md` D5. Sin Let's Encrypt.

## Puertos reservados por la plataforma

| Puerto(s)     | Uso                                    | Bind            |
|---------------|-----------------------------------------|-----------------|
| 22            | SSH                                      | 0.0.0.0         |
| 80 / 443      | Traefik (`network_mode: host`)           | 0.0.0.0         |
| 5432          | Postgres (infra)                         | 127.0.0.1       |
| 6379          | Redis (infra)                            | 127.0.0.1       |
| 7681–7688     | ttyd, un slot por sesión activa (máx. 8) | 127.0.0.1       |
| 8000          | Django panel (uvicorn, Fase 1)           | 127.0.0.1       |
| 20000–29999   | Reservado para `mcp_ports` (Fase 4, lo asignan los propios agentes) | 127.0.0.1 |

No se abre ningún puerto de aplicación al 0.0.0.0 salvo 80/443 (Traefik, único
punto de entrada) y 22 (SSH).

## RAM/CPU base (Gate 0)

Medido 2026-07-18 en el VPS (4 vCPU / 7.94 GB RAM), infra levantada:

| Estado                                   | RAM usada | Disponible |
|------------------------------------------|-----------|------------|
| Infra sola (Traefik+PG+Redis) + shell    | ~764 MB   | ~7.1 GB    |
| Infra + 1 `claude` idle                  | ~801 MB   | ~7.1 GB    |
| Infra + **9 `claude` idle** (escotilla)  | ~1730 MB  | ~6.2 GB    |

- ~116 MB incrementales por sesión `claude` idle en tmux.
- Load average con 8 sesiones recién lanzadas: ~0.69 sobre 4 vCPU; baja a ~0
  cuando terminan de renderizar (idle real).
- Conclusión: el VPS sostiene 8 sesiones de escotilla simultáneas con >6 GB
  libres. Nota: la escotilla (tmux+`claude` CLI) es independiente de los
  workers del Agent SDK (Fase 1); esta medición es solo del camino manual.

## Verificaciones de aislamiento (Gate 0)

- `ss -tlnp`: Postgres (5432) y Redis (6379) escuchan solo en `127.0.0.1`
  (docker-proxy), nunca en `0.0.0.0`.
- Desde el exterior (IP pública `169.58.33.122`): 5432 y 6379
  cerrados/filtrados; solo 22 (SSH) y 443 (Traefik) abiertos. `ufw` activo
  permitiendo únicamente OpenSSH, 80, 443.
