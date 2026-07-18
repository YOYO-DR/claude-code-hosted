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

_Pendiente de completar tras correr el checklist de Gate 0 (8 sesiones tmux
con `claude` idle)._
