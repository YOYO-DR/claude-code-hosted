# claude-code-hosted

Panel web para sesiones headless de Claude Code corriendo en un VPS (Ubuntu
24.04, Traefik + Postgres + Redis + Django ASGI + workers systemd con el
Claude Agent SDK). Ver `PLAN.md` para la arquitectura y las fases.

`legacy/` contiene el scaffold inicial (cookiecutter-django + Vite/React vía
Dokploy) generado antes de arrancar el PLAN.md — se conserva solo como
referencia, no se despliega.

## Estado

En construcción, Fase 0 (`PLAN.md` §5). Ver `PROGRESS.md` y `INFRA.md` una
vez existan.
