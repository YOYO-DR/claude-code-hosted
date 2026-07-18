# claude-code-hosted

Web panel para sesiones headless de Claude Code corriendo en una VPS.

Construido sobre la plantilla `plantilla-django-react` (cookiecutter-django +
Vite/React). Ver `PLAN.md` para la hoja de ruta completa del proyecto y
`AGENTS.md` para orientacion a agentes.

## Requisitos

- Docker + Docker Compose v2
- `just` (https://github.com/casey/just)
- `uv` (https://docs.astral.sh/uv/)
- `pnpm` (frontend)

## Levantar en local

```bash
just build                    # docker compose build
just up                       # django, postgres, pgbouncer, redis, celery*, flower, frontend
```

Frontend en http://localhost:5173 y backend en http://localhost:8000.

## Comandos utiles

```bash
just manage +args             # docker compose run --rm django python manage.py <args>
just manage-direct-db createsuperuser
just pytest +args
just logs celeryworker
just down
```

## Documentacion

- `PLAN.md` — fases del proyecto, gates de validacion.
- `AGENTS.md` — guia para agentes (estructura, comandos, cosas que rompen silenciosamente).
- `START.md` — guia de rename a partir de la plantilla base (conservada por referencia).
