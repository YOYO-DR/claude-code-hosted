# Plan: Copiar config Docker/despliegue de yodumanager-v2 a plantilla-django-react

**Rama objetivo:** `main` (proyecto nuevo, sin cambios previos relevantes)
**Fecha:** 2026-06-14
**Estado:** Pendiente de revisión (NO se ha editado código aún)
**Origen del análisis:** `/home/yoiner/Escritorio/programacion/proyectos/yodumanager-v2`
**Destino del plan:** `/home/yoiner/Escritorio/programacion/proyectos/plantilla-django-react`

---

## 1. Resumen ejecutivo

`yodumanager-v2` y `plantilla-django-react` comparten el mismo esqueleto base
(Django Cookiecutter). La diferencia es que `plantilla-django-react` está
incompleto a nivel de despliegue: **no tiene `frontend` en el compose, no
tiene `docker-compose.dokploy.yml`, no tiene configs de CORS, no tiene
start-frontend con wait-for-django, y no tiene nginx seguro para producción**.

El plan copia la configuración probada de yodumanager-v2 manteniendo lo
bueno que ya tiene plantilla (UV, Python 3.14, `/app/.venv` como volumen
anónimo) y descarta lo que no aplica.

**Lo que el usuario pidió explícitamente:**
- Mantener **UV** para Django.
- Configurar **frontend + backend + celery (worker/beat/flower) + pgbouncer**.
- Crear **docker-compose.dokploy.yml**.
- **Implementar autenticación DRF SimpleJWT completa** (no solo login de prueba).
- **Eliminar axios** del frontend; crear un servicio **fetch-based con auto-refresh
  + cola de peticiones** durante el refresh, **reutilizable como base
  para muchos proyectos** (genérico, sin dependencias externas).
- Crear login funcional que valide CORS **y** el flujo end-to-end de SimpleJWT.

---

## 2. Diagnóstico comparativo

### 2.1 Lo que plantilla YA tiene bien (NO se toca)

| Pieza | Estado | Archivo |
| --- | --- | --- |
| UV como gestor de deps Python | ✅ ya configurado | `backend/compose/local/django/Dockerfile:2,12,24-34` |
| `uv.lock` montado con `--mount=type=bind` | ✅ cache-friendly | `backend/compose/local/django/Dockerfile:25-26` |
| Volumen anónimo `/app/.venv` | ✅ evita que el bind-mount pise el venv | `docker-compose.local.yml:17` |
| `obtain_auth_token` en DRF | ✅ listo para probar CORS | `backend/config/urls.py:75` |
| `django-cors-headers` instalado y en MIDDLEWARE | ✅ base ya está | `backend/config/settings/base.py:84,139,342` |
| `corsheaders.middleware.CorsMiddleware` en 2º puesto | ✅ posición correcta | `backend/config/settings/base.py:137-149` |
| `pyproject.toml` con django-cors-headers 4.9.0 | ✅ | `backend/pyproject.toml:23` |
| `merge_production_dotenvs_in_dotenv.py` | ✅ existe | `backend/merge_production_dotenvs_in_dotenv.py` |
| `.envs/.local` y `.envs/.production` con `.django`/`.postgres` | ✅ estructura | `backend/.envs/` |
| `.dockerignore` con `.venv/` y `**/__pycache__/` | ✅ | `backend/.dockerignore` |

### 2.2 Lo que plantilla NO tiene y SÍ necesita (lo que se va a copiar)

| Falta en plantilla | Existe en yodumanager-v2 | Acción |
| --- | --- | --- |
| Servicio `frontend` en compose local | `docker-compose.local.yml:107-122` | **Crear** |
| `frontend/compose/local/Dockerfile` | `frontend/compose/local/Dockerfile` | **Crear** (basado en yodumanager, pnpm) |
| `frontend/compose/production/Dockerfile` (multi-stage) | `frontend/compose/production/Dockerfile` | **Crear** |
| `frontend/compose/production/nginx/nginx.conf` con headers de seguridad | `frontend/compose/production/nginx/nginx.conf` | **Crear** |
| `frontend/start-frontend` con `wait-for-it django:8000` | `frontend/start-frontend` | **Crear** |
| `frontend/.dockerignore` | `frontend/.dockerignore` | **Crear** |
| `frontend/.env` con `VITE_API_URL` | `frontend/.env` | **Crear** |
| `vite.config.js` con `host: "0.0.0.0"` (necesario para CORS desde otro host) | `frontend/vite.config.js:38` | **Editar** |
| `docker-compose.dokploy.yml` con red externa `dokploy-network` | `docker-compose.dokploy.yml` | **Crear** |
| `DJANGO_CORS_ALLOWED_ORIGINS` y `DJANGO_CSRF_TRUSTED_ORIGINS` en envs | `.envs/.local/.django:6-7` | **Añadir** |
| `DJANGO_NAME_LOG_FILE` y creación de `/app/logs` | `.envs/.local/.django:8` + `compose/local/django/Dockerfile:7` | **Añadir** |
| Login simple en frontend que dispare CORS | (no existe en yodumanager, es nuevo) | **Crear** |

### 2.3 Análisis de Celery y pgbouncer (lo que faltaba)

#### 2.3.1 Celery — diff real entre proyectos

`diff -r` de `compose/local/django/celery/` y `compose/production/django/celery/`:

| Archivo | Estado | Detalle |
| --- | --- | --- |
| `local/celery/worker/start` | ✅ Idéntico | — |
| `local/celery/beat/start` | ✅ Idéntico | — |
| `local/celery/flower/start` | ✅ Idéntico | — |
| `production/celery/worker/start` | ⚠️ Falta `mkdir -p /app/logs` | yodumanager línea 7, plantilla vacío entre línea 6 y 8 |
| `production/celery/beat/start` | ⚠️ Falta `mkdir -p /app/logs` | mismo patrón |
| `production/celery/flower/start` | ⚠️ Falta `mkdir -p /app/logs` | mismo patrón |
| `config/celery_app.py` | ✅ Funcionalmente idéntico | única diferencia: `app = Celery("plantilla_django_react")` vs `Celery("backend")` — solo cambia el nombre lógico del worker, no la config. Sin acción. |

**Conclusión:** solo hay que añadir `mkdir -p /app/logs` a los 3 scripts
de producción. La estructura de Celery local ya está completa (los 3
servicios: worker, beat, flower) y se van a añadir también al compose
de Dokploy que no los tenía.

#### 2.3.2 pgbouncer — no existe en plantilla

`ls backend/compose/production/pgbouncer/` → no existe.

yodumanager trae 4 archivos:

| Archivo | Propósito |
| --- | --- |
| `Dockerfile` | Base `edoburu/pgbouncer:v1.24.1-p1` + `gettext` (envsubst) + usuario pgbouncer + entrypoint custom |
| `entrypoint.sh` | Genera `pgbouncer.ini` y `userlist.txt` con `envsubst` desde templates |
| `pgbouncer.ini.template` | Config de pgbouncer con placeholders `${POSTGRES_*}` |
| `userlist.template.txt` | `auth_file` con `${POSTGRES_USER}` y `${POSTGRES_PASSWORD}` |

Se copian los 4 tal cual, ya que no tienen dependencias del proyecto
(son agnósticos a yodumanager). La plantilla actual usa Postgres directo,
sin pgbouncer, lo que es problemático en producción con múltiples
workers de Celery + Gunicorn (cada uno abre conexiones Postgres sin
pool). yodumanager introduce pgbouncer con `pool_mode = transaction`,
`max_client_conn = 1000`, `default_pool_size = 80` — el plan lo replica
idéntico.

#### 2.3.3 Variables de entorno necesarias para pgbouncer

yodumanager tiene en su `.envs/.local/.postgres`:
```ini
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=yodumanager
POSTGRES_USER=debug
POSTGRES_PASSWORD=debug

PGB_POSTGRES_HOST=pgbouncer
PGB_POSTGRES_PORT=6432
PGB_POSTGRES_DB=${POSTGRES_DB}
PGB_POSTGRES_USER=${POSTGRES_USER}
PGB_POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
```

plantilla actual solo tiene las 5 primeras (sin el bloque `PGB_*`).
Hay que añadir las 5 `PGB_*` a `backend/.envs/.local/.postgres` y
`backend/.envs/.production/.postgres`.

Y el `DATABASE_URL` de Django (en `.django`) debe apuntar a pgbouncer,
no a postgres. En plantilla actual **no existe `DATABASE_URL`** — se
construye desde variables `POSTGRES_*` vía `env.db("DATABASE_URL")` en
`base.py:49`. Hay que **crear** la var `DATABASE_URL` en los `.django`.

#### 2.3.4 Entrypoint de Django (producción) — actualizar

El actual usa `POSTGRES_HOST` y `POSTGRES_PORT` directos. Con pgbouncer
debe usar `PGB_POSTGRES_HOST` y `PGB_POSTGRES_PORT`. Es la misma lógica
que yodumanager:11.

### 2.4 Lo que plantilla NO tiene y NO se va a copiar (fuera de scope)

| Pieza de yodumanager | Razón para NO copiar |
| --- | --- |
| `flower-secure-router` en traefik | No hay flower |
| `policies/limits` con `DJANGO_CPU`, `DJANGO_RAM` | Se puede añadir después |
| `frontend1/` (legacy) | No existe en plantilla |
| `mobile/` (Expo) | No existe en plantilla |
| Resend / Gmail credenciales | No es parte de infra |
| `APP_HOME=/app/apps/...` (ruta apps/) | Plantilla usa `/app/plantilla_django_react/` (estructura cookiecutter estándar). Se conserva. |

---

## 3. Enfoque elegido

**Copiar la infra de yodumanager-v2 (compose, frontend, pgbouncer, celery),
mantener UV, e implementar autenticación real con DRF SimpleJWT** en lugar
de `obtain_auth_token`.

### 3.1 Backend: DRF SimpleJWT con refresh token en cookie HttpOnly

Patrón copiado de yodumanager-v2 (`apps/custom_auth/`):
- Login: `POST /api/auth/token` → `{access, user}` (refresh va en cookie HttpOnly).
- Refresh: `POST /api/auth/token/refresh` → lee refresh de la cookie, devuelve nuevo access.
- Logout: `POST /api/auth/logout` → borra la cookie.
- Register: `POST /api/auth/register/` → crea usuario.
- Forgot password: `POST /api/auth/forgot-password/`.

**Por qué refresh en cookie HttpOnly y no en localStorage:**
- La cookie HttpOnly **no es accesible por JS** → mitiga XSS stealing tokens.
- El access token (5 min) sí va en `localStorage` (memoria del navegador
  es accesible por XSS, pero su vida útil corta limita el daño).
- `withCredentials: 'include'` en `fetch` + `CORS_ALLOW_CREDENTIALS = True`
  en Django → la cookie se envía en cada request cross-origin.

> **Alternativa descartada:** access + refresh ambos en localStorage.
> Más simple de implementar pero XSS-roof. Se descarta por seguridad.

### 3.2 Frontend: servicio `apiClient` con fetch + auto-refresh

Patrón de auto-refresh + queue copiado de `yodumanager-v2/frontend/src/api/axiosService.js`,
pero **reescrito en fetch puro** (sin axios, sin dependencias) y
**diseñado como librería base reusable**:

- Singleton a nivel de módulo (igual que axios en yodumanager).
- Inyecta `Authorization: Bearer <access>` en cada request.
- Si una request devuelve 401 y **no** es login/refresh/verify:
  1. Si ya hay un refresh en curso → la request entra en cola (`Promise`).
  2. Si no → marca la request como `_retry`, llama a `refresh`, reintenta.
- Si el refresh falla → limpia tokens, dispara evento `auth:logout` para
  que la app redirija a login.
- Métodos: `api.get`, `api.post`, `api.put`, `api.patch`, `api.delete`,
  `api.setTokens({access, refresh})`, `api.clearTokens()`,
  `api.onUnauthorized(callback)`.

**Diferencias con `axiosService.js` de yodumanager:**

| Aspecto | yodumanager (axios) | plan (fetch) |
| --- | --- | --- |
| Dependencia | `axios` (40KB+) | `fetch` nativo (0KB) |
| Refresh token | Cookie HttpOnly via `withCredentials` | Idéntico |
| Storage access token | Zustand persist (`localStorage`) | Idéntico |
| Storage refresh token | Solo en cookie HttpOnly (no en JS) | Idéntico |
| Singleton | `axios.create` | `class ApiClient` exportada |
| Cola de refresh | Array de `{resolve, reject}` | Idéntico |
| CORS | `axios.post` directo para refresh | `fetch` con `credentials: 'include'` |
| Error 403 → toast de upgrade | Sí | Sí (igual, opcional, configurable) |
| Reusabilidad | Acoplado a zustand + react | **Genérico**: zustand es optativo, configurable vía `api.onTokenChange(cb)` |

### 3.3 App `custom_auth` en backend

Se crea como **app local** de plantilla (en `plantilla_django_react/custom_auth/`,
NO en `apps/` que es convención de yodumanager) para seguir la
estructura cookiecutter del proyecto. Solo se trae lo esencial:
- `api/viewsets/token.py` (login, refresh, logout)
- `api/serializers/token.py` (custom token serializer con datos de usuario)
- `api/router.py`

`register` y `forgot-password` **se omiten** en este plan (no son
necesarios para el flujo de CORS + login). Se pueden añadir después
copiando los viewsets de yodumanager.

---

## 4. Cambios detallados

### 4.1 Backend: `.envs/.local/.django` (editar)

Añadir las variables de CORS/CSRF/log que ya están en yodumanager:

```ini
# .envs/.local/.django — plantilla-django-react (EDITAR)
USE_DOCKER=yes
IPYTHONDIR=/app/.ipython
DJANGO_SECRET_KEY=local-development-secret-key-not-for-production
DJANGO_CSRF_TRUSTED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
DJANGO_CORS_ALLOWED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
DJANGO_NAME_LOG_FILE=django_dev
DJANGO_DEBUG=true
REDIS_URL=redis://redis:6379/0
CELERY_FLOWER_USER=debug
CELERY_FLOWER_PASSWORD=debug
```

**Por qué:** sin `DJANGO_CORS_ALLOWED_ORIGINS`, el middleware de
cors-headers bloquea los `OPTIONS` preflight del frontend. Sin
`DJANGO_CSRF_TRUSTED_ORIGINS`, falla CSRF en POST cross-origin.

### 4.2 Backend: `.envs/.production/.django` (editar)

Igual que local pero con `DJANGO_DEBUG=False` y los origins de
producción (placeholder):

```ini
# .envs/.production/.django — plantilla-django-react (EDITAR)
DJANGO_SETTINGS_MODULE=config.settings.production
DJANGO_SECRET_KEY=<generar uno nuevo>
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=.plantilla.yoyodr.dev
DJANGO_ADMIN_URL=BPWPq0SDOy6rSlfMaA5xu8gc0gf9dw36/
DJANGO_CSRF_TRUSTED_ORIGINS=https://plantilla.yoyodr.dev
DJANGO_CORS_ALLOWED_ORIGINS=https://plantilla.yoyodr.dev
DJANGO_NAME_LOG_FILE=django_prod
DJANGO_SECURE_SSL_REDIRECT=False
DJANGO_ACCOUNT_ALLOW_REGISTRATION=True
WEB_CONCURRENCY=4
REDIS_URL=redis://redis:6379/0
CELERY_FLOWER_USER=debug
CELERY_FLOWER_PASSWORD=debug
```

**Nota:** la URL exacta de Dokploy se decide en despliegue. Aquí dejo
el dominio de la `.gitignore` que ya tiene plantilla.

### 4.3 Backend: `compose/local/django/Dockerfile` (editar)

Solo añadir creación de `/app/logs` (al estilo yodumanager:38) para que
el `start` no falle al hacer `mkdir -p`. La parte de UV se queda **intacta**:

```dockerfile
# ... (lo que ya hay, sin tocar) ...

# Después de los RUN apt-get y antes de COPY . ${APP_HOME}:
RUN mkdir -p ${APP_HOME}/logs
```

(Si se prefiere, se mete dentro de `compose/local/django/start` como en
yodumanager:7 — `mkdir -p /app/logs` antes del `migrate`. El plan
recomienda añadirlo al Dockerfile para que el directorio exista aunque
se cambie el script `start` en el futuro.)

### 4.4 Backend: `compose/production/django/Dockerfile` (editar)

El actual ya hace `mkdir -p ${APP_HOME}/plantilla_django_react/media`
(línea 80). Añadir `logs` en la misma línea:

```dockerfile
# Cambiar línea 80:
RUN mkdir -p ${APP_HOME}/plantilla_django_react/media \
    && mkdir -p ${APP_HOME}/logs
```

### 4.4a `backend/compose/production/django/entrypoint` (editar)

Actualizar para usar `PGB_POSTGRES_*` (al estilo yodumanager:7-13). El
actual usa `POSTGRES_*` directos y no espera a pgbouncer:

```bash
#!/bin/bash

set -o errexit
set -o pipefail
set -o nounset

if [ -z "${PGB_POSTGRES_USER}" ]; then
    base_postgres_image_default_user='postgres'
    export PGB_POSTGRES_USER="${base_postgres_image_default_user}"
fi
export DATABASE_URL="postgres://${PGB_POSTGRES_USER}:${PGB_POSTGRES_PASSWORD}@${PGB_POSTGRES_HOST}:${PGB_POSTGRES_PORT}/${POSTGRES_DB}"

wait-for-it "${PGB_POSTGRES_HOST}:${PGB_POSTGRES_PORT}" -t 30

>&2 echo 'pgBouncer is available'

exec "$@"
```

> **Decisión:** el entrypoint es para el **contenedor de Django**, no
> para pgbouncer. Django espera a que pgbouncer esté arriba antes de
> arrancar. En local el `start` script ya hace `migrate` antes que
> uvicorn, así que el entrypoint es solo para producción.

### 4.4b Celery — fixes de producción (editar 3 archivos)

**Patrón único:** añadir `mkdir -p /app/logs` después de la línea
`set -o nounset` en cada uno de los 3 scripts de producción. (Los de
local no se tocan — diff dice que son idénticos a yodumanager).

#### `compose/production/django/celery/worker/start` (editar)

```bash
#!/bin/bash

set -o errexit
set -o pipefail
set -o nounset

mkdir -p /app/logs

exec celery -A config.celery_app worker -l INFO
```

#### `compose/production/django/celery/beat/start` (editar)

```bash
#!/bin/bash

set -o errexit
set -o pipefail
set -o nounset

mkdir -p /app/logs

exec celery -A config.celery_app beat -l INFO
```

#### `compose/production/django/celery/flower/start` (editar)

```bash
#!/bin/bash

set -o errexit
set -o nounset

mkdir -p /app/logs

until timeout 10 celery -A config.celery_app inspect ping; do
    >&2 echo "Celery workers not available"
done

echo 'Starting flower'

exec celery \
    -A config.celery_app \
    -b "${REDIS_URL}" \
    flower \
    --basic_auth="${CELERY_FLOWER_USER}:${CELERY_FLOWER_PASSWORD}"
```

> **Decisión:** el `flower/start` en local usa `watchfiles` para
> HMR. En producción no, porque no hay reload. No se toca local.

### 4.4c pgbouncer (CREAR 4 archivos)

Carpeta nueva: `backend/compose/production/pgbouncer/`. Los 4 archivos
se copian **literales** de yodumanager — son agnósticos al nombre del
proyecto.

#### `backend/compose/production/pgbouncer/Dockerfile` (CREAR)

```dockerfile
FROM edoburu/pgbouncer:v1.24.1-p1

USER root

RUN apk add --no-cache gettext \
    && addgroup -S pgbouncer \
    && adduser -S -G pgbouncer pgbouncer

COPY ./compose/production/pgbouncer/pgbouncer.ini.template /etc/pgbouncer/pgbouncer.ini.template
COPY ./compose/production/pgbouncer/userlist.template.txt /etc/pgbouncer/userlist.template.txt
COPY ./compose/production/pgbouncer/entrypoint.sh /entrypoint-custom.sh

RUN chmod +x /entrypoint-custom.sh
RUN chown -R pgbouncer:pgbouncer /etc/pgbouncer
RUN mkdir -p /var/log/pgbouncer
RUN chown pgbouncer:pgbouncer /var/log/pgbouncer
RUN mkdir -p /var/run/pgbouncer
RUN chown pgbouncer:pgbouncer /var/run/pgbouncer

USER pgbouncer
EXPOSE 5432
ENTRYPOINT ["/entrypoint-custom.sh"]
CMD ["pgbouncer", "/etc/pgbouncer/pgbouncer.ini"]
```

#### `backend/compose/production/pgbouncer/entrypoint.sh` (CREAR)

```sh
#!/bin/sh
set -e

if [ -z "$POSTGRES_USER" ] || [ -z "$POSTGRES_PASSWORD" ]; then
    echo "Error: POSTGRES_USER and POSTGRES_PASSWORD environment variables are required"
    exit 1
fi

echo "Generating userlist.txt from template..."
envsubst < /etc/pgbouncer/userlist.template.txt > /etc/pgbouncer/userlist.txt
envsubst < /etc/pgbouncer/pgbouncer.ini.template > /etc/pgbouncer/pgbouncer.ini
chmod 600 /etc/pgbouncer/userlist.txt

echo "Generated userlist.txt content:"
cat /etc/pgbouncer/userlist.txt

echo "Starting PgBouncer..."

exec "$@"
```

#### `backend/compose/production/pgbouncer/pgbouncer.ini.template` (CREAR)

```ini
[databases]
${POSTGRES_DB} = host=${POSTGRES_HOST} port=${POSTGRES_PORT} dbname=${POSTGRES_DB} user=${POSTGRES_USER} password=${POSTGRES_PASSWORD}

[pgbouncer]
listen_addr = 0.0.0.0
listen_port = 6432
auth_type = md5
auth_file = /etc/pgbouncer/userlist.txt
pool_mode = transaction
max_client_conn = 1000
default_pool_size = 80
min_pool_size = 30
reserve_pool_size = 10
reserve_pool_timeout = 5
logfile = /var/log/pgbouncer/pgbouncer.log
pidfile = /var/run/pgbouncer/pgbouncer.pid
admin_users = ${POSTGRES_USER}
```

#### `backend/compose/production/pgbouncer/userlist.template.txt` (CREAR)

```
"${POSTGRES_USER}" "${POSTGRES_PASSWORD}"
```

> **Decisión sobre puerto:** pgbouncer escucha en 6432 (default de
> pgbouncer). El `EXPOSE` en Dockerfile dice 5432 (default postgres)
> pero es solo metadato — el CMD usa `pgbouncer.ini` que dice 6432.

### 4.4d `backend/.envs/.local/.postgres` (editar)

Añadir el bloque `PGB_*` que ya existe en yodumanager:11-15. Estado
actual de plantilla: solo 5 vars `POSTGRES_*`.

```ini
# .envs/.local/.postgres — plantilla-django-react (EDITAR)
# PostgreSQL
# ------------------------------------------------------------------------------
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=plantilla_django_react
POSTGRES_USER=debug
POSTGRES_PASSWORD=debug

# pgbouncer
# ------------------------------------------------------------------------------
PGB_POSTGRES_HOST=pgbouncer
PGB_POSTGRES_PORT=6432
PGB_POSTGRES_DB=${POSTGRES_DB}
PGB_POSTGRES_USER=${POSTGRES_USER}
PGB_POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
```

### 4.4e `backend/.envs/.production/.postgres` (editar)

Idéntico al local, con el hostname del Postgres real de Dokploy:

```ini
# .envs/.production/.postgres — plantilla-django-react (EDITAR)
POSTGRES_HOST=<hostname del postgres de Dokploy>
POSTGRES_PORT=5432
POSTGRES_DB=plantilla_django_react
POSTGRES_USER=debug
POSTGRES_PASSWORD=debug

PGB_POSTGRES_HOST=pgbouncer
PGB_POSTGRES_PORT=6432
PGB_POSTGRES_DB=${POSTGRES_DB}
PGB_POSTGRES_USER=${POSTGRES_USER}
PGB_POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
```

> **Decisión:** en Dokploy, el contenedor `pgbouncer` se conecta al
> Postgres de Dokploy por `dokploy-network`. El valor de
> `POSTGRES_HOST` se inyecta en runtime. El placeholder lo rellena
> Dokploy al desplegar.

### 4.4f `backend/.envs/.local/.django` y `.production/.django` (editar)

Añadir `DATABASE_URL` apuntando a pgbouncer. Estado actual: no existe
`DATABASE_URL` (Django lo construye de `POSTGRES_*` sueltos en
`base.py:49` con `env.db("DATABASE_URL")`, lo que **falla** si la var
no está — por eso hay que añadirla).

```ini
# En .envs/.local/.django añadir:
DATABASE_URL=postgres://${PGB_POSTGRES_USER}:${PGB_POSTGRES_PASSWORD}@${PGB_POSTGRES_HOST}:${PGB_POSTGRES_PORT}/${PGB_POSTGRES_DB}
```

> **Por qué apuntar a pgbouncer y no a postgres directo:** en local
> con Django + Celery + 2 workers, sin pool las conexiones se acumulan
> rápido. pgbouncer las multiplexa. Es el patrón de yodumanager y es
> la forma correcta de correr Postgres en compose de desarrollo con
> varios workers.
>
> **Nota para `manage.py migrate` en local:** las migraciones deben
> ir **directas a postgres**, no a pgbouncer (pgbouncer con
> `pool_mode = transaction` no soporta sentencias DDL largas). Por
> eso el justfile de yodumanager tiene `manage-direct-db` que
> sobreescribe `PGB_POSTGRES_HOST=postgres` y `PGB_POSTGRES_PORT=5432`.
> El plan añade la misma receta al justfile de plantilla (Paso 5b).

### 4.5 `docker-compose.local.yml` (editar — añadir `pgbouncer` y `frontend`)

El compose actual ya tiene `celeryworker`, `celerybeat`, `flower` y
`postgres` directo. Hay que:

1. Insertar `pgbouncer` entre `postgres` y los consumidores (siguiendo
   yodumanager:42-54).
2. Cambiar `depends_on: [postgres, redis]` → `[pgbouncer, redis]` en
   `django`, `celeryworker`, `celerybeat`, `flower`.
3. Añadir `frontend` al final.

```yaml
# Insertar tras el servicio redis:
  pgbouncer:
    build:
      context: .
      dockerfile: ./compose/production/pgbouncer/Dockerfile
    image: plantilla_django_react_local_pgbouncer
    container_name: plantilla_django_react_local_pgbouncer
    restart: unless-stopped
    depends_on:
      - postgres
    env_file:
      - ./.envs/.local/.postgres
    ports:
      - '6432:6432'

# Cambiar depends_on en django de:
    depends_on:
      - postgres
      - redis
# a:
    depends_on:
      - pgbouncer
      - redis

# Mismo cambio en celeryworker, celerybeat, flower:
    depends_on:
      - pgbouncer
      - redis

# Añadir al final:
  frontend:
    build:
      context: ./frontend
      dockerfile: ./compose/local/Dockerfile
    image: plantilla_django_react_local_frontend
    container_name: plantilla_django_react_local_frontend
    volumes:
      - ./frontend:/app
      - /app/node_modules
    env_file:
      - ./frontend/.env
    ports:
      - '5173:5173'
    depends_on:
      - django
    restart: unless-stopped
```

**Decisión sobre puerto:** mantengo `5173` (puerto por defecto de Vite),
no `8080` como yodumanager, porque en plantilla el `vite.config.js`
actual y el dev server esperan 5173. Si se quiere 8080, se cambia en
`vite.config.js` también.

**Decisión sobre `pgbouncer` en local:** se incluye porque
yodumanager lo tiene y el patrón es consistente entre local y
producción (un único `DATABASE_URL`). Sin pgbouncer local, cada vez
que un dev arranca la app con celeryworker + django + flower se
acumulan conexiones a postgres — pgbouncer las gestiona.

### 4.6 `docker-compose.production.yml` (editar — añadir pgbouncer y frontend)

El compose actual tiene `postgres` y `redis` pero no `pgbouncer`. Hay
que insertar pgbouncer entre `django` y `postgres`, y los servicios de
celery deben depender de `pgbouncer` (no de `postgres`).

```yaml
# Añadir nuevo servicio pgbouncer (insertar tras postgres):
  pgbouncer:
    build:
      context: .
      dockerfile: ./compose/production/pgbouncer/Dockerfile
    image: plantilla_django_react_production_pgbouncer
    restart: unless-stopped
    depends_on:
      - postgres
    env_file:
      - ./.envs/.production/.postgres

# Cambiar depends_on del servicio django de:
    depends_on:
      - postgres
      - redis
# a:
    depends_on:
      - pgbouncer
      - redis

# Mismo cambio para celeryworker, celerybeat y flower:
    depends_on:
      - pgbouncer
      - redis

# Añadir al final (siguiendo el orden de yodumanager):
  frontend:
    build:
      context: ./frontend
      dockerfile: ./compose/production/Dockerfile
      args:
        - VITE_API_URL=${VITE_API_URL}
    image: plantilla_django_react_production_frontend
    depends_on:
      - django
    restart: unless-stopped
```

> **Decisión sobre flower en `docker-compose.production.yml`:** la
> versión actual de plantilla **ya tiene** flower definido (líneas
> 70-73). Se mantiene, pero se le cambia `depends_on` para usar
> pgbouncer.

### 4.7 `docker-compose.dokploy.yml` (CREAR nuevo)

Copia adaptada de yodumanager:7-30, **sin** traefik propio (Dokploy
trae el suyo) pero **CON** pgbouncer y celery.

```yaml
# docker-compose.dokploy.yml
volumes:
  production_traefik: {}
  production_django_media: {}
  production_django_logs: {}

services:
  django: &django
    build:
      context: .
      dockerfile: ./compose/production/django/Dockerfile
    image: plantilla_django_react_production_django
    volumes:
      - production_django_media:/app/plantilla_django_react/media
      - production_django_logs:/app/logs
    restart: unless-stopped
    env_file:
      - ./.envs/.production/.django
      - ./.envs/.production/.postgres
    command: /start
    deploy:
      resources:
        reservations:
          cpus: '0.5'
          memory: 256M
        limits:
          cpus: ${DJANGO_CPU:-1}
          memory: ${DJANGO_RAM:-1g}
    networks:
      - dokploy-network
      - default

  pgbouncer:
    build:
      context: .
      dockerfile: ./compose/production/pgbouncer/Dockerfile
    image: plantilla_django_react_production_pgbouncer
    restart: unless-stopped
    env_file:
      - ./.envs/.production/.postgres
    depends_on:
      - postgres
    networks:
      - dokploy-network
      - default
    deploy:
      resources:
        reservations:
          cpus: '0.1'
          memory: 123M
        limits:
          cpus: ${PGBOUNCER_CPU:-1}
          memory: ${PGBOUNCER_RAM:-1g}

  celeryworker:
    <<: *django
    image: plantilla_django_react_production_celeryworker
    command: /start-celeryworker
    ports: []
    restart: unless-stopped
    env_file:
      - ./.envs/.production/.django
      - ./.envs/.production/.postgres
    depends_on:
      - pgbouncer
      - redis
    deploy:
      replicas: ${CANT_WORKERS_CELERY:-2}
      resources:
        reservations:
          cpus: '0.5'
          memory: 256M
        limits:
          cpus: ${CELERYWORKER_CPU:-1}
          memory: ${CELERYWORKER_RAM:-1g}
    networks:
      - dokploy-network
      - default

  celerybeat:
    <<: *django
    image: plantilla_django_react_production_celerybeat
    restart: unless-stopped
    volumes:
      - production_django_media:/app/plantilla_django_react/media
    command: /start-celerybeat
    env_file:
      - ./.envs/.production/.django
      - ./.envs/.production/.postgres
    depends_on:
      - pgbouncer
      - redis
    deploy:
      resources:
        reservations:
          cpus: '0.5'
          memory: 256M
        limits:
          cpus: ${CELERYBEAT_CPU:-1}
          memory: ${CELERYBEAT_RAM:-1g}
    networks:
      - dokploy-network
      - default

  flower:
    <<: *django
    image: plantilla_django_react_production_flower
    command: /start-flower
    env_file:
      - ./.envs/.production/.django
      - ./.envs/.production/.postgres
    depends_on:
      - celeryworker
    networks:
      - dokploy-network
      - default

  nginx:
    build:
      context: .
      dockerfile: ./compose/production/nginx/Dockerfile
    image: plantilla_django_react_production_nginx
    depends_on:
      - django
    volumes:
      - production_django_media:/usr/share/nginx/media:ro
    restart: unless-stopped
    deploy:
      resources:
        reservations:
          cpus: '0.1'
          memory: 123M
        limits:
          cpus: ${NGINX_CPU:-1}
          memory: ${NGINX_RAM:-1g}
    networks:
      - dokploy-network
      - default

  frontend:
    build:
      context: ./frontend
      dockerfile: ./compose/production/Dockerfile
      args:
        - VITE_API_URL=${VITE_API_URL}
    image: plantilla_django_react_production_frontend
    restart: unless-stopped
    depends_on:
      - django
    deploy:
      resources:
        reservations:
          cpus: '0.5'
          memory: 256M
        limits:
          cpus: ${FRONTEND_CPU:-1}
          memory: ${FRONTEND_RAM:-1g}
    networks:
      - dokploy-network
      - default

networks:
  dokploy-network:
    external: true
```

**Por qué sin `traefik` propio:** Dokploy ya inyecta Traefik y resuelve
DNS a los contenedores del stack. No tiene sentido tener uno dentro. El
mapeo de dominios se hace desde la UI de Dokploy (`Host` → servicio
`django:5000`, `Host` → servicio `frontend:80`, `PathPrefix(/media/)` →
servicio `nginx:80`).

**Decisión sobre `postgres` en Dokploy:** yodumanager conecta
directamente al Postgres de Dokploy vía `dokploy-network` (no declara
servicio `postgres` en su compose). En el plan actual, el `postgres`
**no** se incluye como servicio en el compose de Dokploy — se asume
que Dokploy ya aprovisionó una instancia de Postgres en la misma red.
El `pgbouncer` se conecta al hostname que se le pase por env
(`POSTGRES_HOST`), que en producción apuntará al Postgres de Dokploy, no
a `postgres:5432`.

### 4.8 `frontend/compose/local/Dockerfile` (CREAR)

Copiado de yodumanager:1-23 con pnpm y corepack. Mantener Alpine 3.23
(ya validado en yodumanager).

```dockerfile
# frontend/compose/local/Dockerfile
FROM node:24.12.0-alpine3.23

WORKDIR /app

RUN corepack enable

COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY . .

EXPOSE 5173

# entrypoint espera a django y luego arranca vite
COPY ./start-frontend /start-frontend
RUN sed -i 's/\r$//g' /start-frontend && chmod +x /start-frontend
CMD ["/start-frontend"]
```

### 4.9 `frontend/compose/production/Dockerfile` (CREAR)

Multi-stage con nginx seguro. Basado en yodumanager:1-55.

```dockerfile
# frontend/compose/production/Dockerfile

# Stage 1: deps
FROM node:24.12.0-alpine3.23 AS deps
WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

# Stage 2: build
FROM node:24.12.0-alpine3.23 AS build
WORKDIR /app
RUN corepack enable
COPY --from=deps /app/node_modules ./node_modules
COPY . .

ARG VITE_API_URL
RUN echo "VITE_API_URL=${VITE_API_URL}" > .env
RUN cat .env
RUN pnpm run build -- --mode production

# Stage 3: nginx
FROM nginx:1.28.1-alpine3.23
RUN apk add --no-cache --update curl && rm -rf /var/cache/apk/*

COPY compose/production/nginx/nginx.conf /etc/nginx/nginx.conf
COPY --from=build --chown=nginx:nginx /app/dist /usr/share/nginx/html

RUN chmod -R 755 /usr/share/nginx/html \
    && chown -R nginx:nginx /var/cache/nginx \
    && touch /var/run/nginx.pid \
    && chown -R nginx:nginx /var/run/nginx.pid

HEALTHCHECK --interval=30s --timeout=3s \
    CMD curl --fail http://localhost:80 || exit 1

USER nginx
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
```

### 4.10 `frontend/compose/production/nginx/nginx.conf` (CREAR)

Copia literal de yodumanager:1-86. Ya tiene todos los headers de
seguridad (X-Frame-Options, X-Content-Type-Options, CSP, HSTS
informativo, Permissions-Policy, fallback SPA a `index.html`).

### 4.11 `frontend/start-frontend` (CREAR)

Basado en yodumanager:1-14. **Decisión clave:** espera a `django:8000`
porque el `docker-compose.local.yml` tiene `depends_on: [django]` pero
eso solo espera a que el contenedor arranque, no a que uvicorn esté
escuchando. Sin este wait, vite puede arrancar antes que Django y el
navegador verá "Network Error" al primer `fetch`.

```bash
#!/bin/bash
set -e

echo "Waiting for Django backend to be ready..."
wait-for-it "django:8000" -t 60

echo "Backend is available, starting Vite..."
exec pnpm run dev -- --host
```

**Nota sobre `pnpm` vs `npm`:** yodumanager usa `npm run dev` en este
script porque su `package.json` no define `packageManager`. Plantilla
sí tiene `pnpm-lock.yaml`, así que uso `pnpm run dev` para coherencia.

### 4.12 `frontend/.dockerignore` (CREAR)

Copiado de yodumanager:

```
node_modules
npm-debug.log
.git
.gitignore
README.md
.env
.env.local
.env.production
.env.development
.nyc_output
coverage
.tmp
.DS_Store
dist
```

### 4.13 `frontend/.env` (CREAR)

```ini
# frontend/.env
# Apunta al backend levantado por docker compose.
# Desde el navegador: localhost:5173 -> necesita CORS habilitado en Django.
# Desde el contenedor del frontend: el host del backend es "django:8000".
VITE_API_URL=http://localhost:8000
```

**Decisión `VITE_API_URL`:** en local el navegador siempre habla a
`http://localhost:8000` (no al nombre del contenedor), porque el
navegador se ejecuta en el host, no en la red de Docker. CORS es
necesario para que Django acepte ese origen.

### 4.14 `frontend/vite.config.js` (editar)

Cambiar la configuración actual mínima a una con `host: "0.0.0.0"`
(necesario para que el contenedor sea accesible desde el host) y
server config que respete el `VITE_API_URL`:

```js
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')

  return {
    plugins: [react()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    server: {
      host: '0.0.0.0',
      port: 5173,
      watch: {
        usePolling: true,
        interval: 300,
      },
    },
  }
})
```

**Decisión sobre `usePolling`:** necesario para que el HMR de Vite
funcione dentro de un contenedor Docker en Linux (los eventos `inotify`
no cruzan los mounts de Docker). Yodumanager lo trae (línea 42-44 de su
`vite.config.js`), se mantiene el patrón.

### 4.15 Backend: DRF SimpleJWT (CREAR app + editar settings)

#### 4.15.1 `backend/pyproject.toml` (editar)

Añadir `djangorestframework-simplejwt` a dependencies:

```toml
dependencies = [
  # ... (existentes) ...
  "djangorestframework-simplejwt==5.5.1",  # https://pypi.org/project/djangorestframework-simplejwt/
  # ... (resto) ...
]
```

> **Decisión de versión:** `5.5.1` (la misma que yodumanager usa,
> validada en producción con Django 5.2). Compatible con DRF 3.17.x.
> Tras añadir, ejecutar `uv lock --upgrade` para regenerar el lock.

#### 4.15.2 `backend/plantilla_django_react/custom_auth/` (CREAR app nueva)

Estructura:

```
plantilla_django_react/custom_auth/
├── __init__.py
├── apps.py
├── api/
│   ├── __init__.py
│   ├── router.py
│   ├── serializers/
│   │   ├── __init__.py
│   │   └── token.py
│   └── viewsets/
│       ├── __init__.py
│       └── token.py
```

> **Decisión sobre ubicación:** en yodumanager la app está en
> `apps/custom_auth/`. En plantilla la convención cookiecutter es
> `plantilla_django_react/<app>/`. Se usa esta última para no romper
> la convención del proyecto.

#### 4.15.3 `backend/plantilla_django_react/custom_auth/apps.py` (CREAR)

```python
from django.apps import AppConfig


class CustomAuthConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "plantilla_django_react.custom_auth"
    label = "custom_auth"
```

> **Por qué `label = "custom_auth"`:** para evitar conflicto con
> `django.contrib.auth` en migraciones / nombres de tabla. Si en el
> futuro se prefiere usar `apps.custom_auth` (estilo yodumanager), se
> migra con `RenameConfig`.

#### 4.15.4 `backend/plantilla_django_react/custom_auth/api/serializers/token.py` (CREAR)

Copia adaptada de yodumanager:7-19 (sin la dependencia de `UserDetailSerializer`
que no existe en plantilla):

```python
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Añade los datos del usuario a la respuesta del login."""

    def validate(self, attrs):
        data = super().validate(attrs)

        # Devuelve el usuario autenticado. En plantilla el User es el
        # default de django-allauth con email como USERNAME_FIELD.
        user = self.user
        data["user"] = {
            "id": user.id,
            "email": user.email,
            "username": getattr(user, "username", None),
            "is_staff": user.is_staff,
            "is_superuser": user.is_superuser,
        }
        return data
```

> **Decisión:** se omite la validación `self.user.status.allows_login`
> porque plantilla no tiene un campo `status` en User. Se documenta
> como hook para añadir cuando se monte la lógica de suscripciones.

#### 4.15.5 `backend/plantilla_django_react/custom_auth/api/viewsets/token.py` (CREAR)

Copia literal de yodumanager:1-90 (3 clases):

```python
from django.conf import settings
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

from ..serializers.token import CustomTokenObtainPairSerializer


class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)

        if response.status_code == status.HTTP_200_OK:
            refresh_token = response.data.get("refresh")

            if refresh_token:
                response.set_cookie(
                    key=settings.JWT_COOKIE_NAME,
                    value=refresh_token,
                    httponly=settings.JWT_COOKIE_HTTP_ONLY,
                    secure=settings.JWT_COOKIE_SECURE,
                    samesite=settings.JWT_COOKIE_SAMESITE,
                    max_age=int(settings.SIMPLE_JWT['REFRESH_TOKEN_LIFETIME'].total_seconds()),
                )
                if "refresh" in response.data:
                    del response.data["refresh"]
        return response


class CookieTokenRefreshView(TokenRefreshView):
    def post(self, request, *args, **kwargs):
        refresh_token = request.COOKIES.get(settings.JWT_COOKIE_NAME)

        data = request.data
        if hasattr(data, 'dict'):
            data = data.dict()
        elif isinstance(data, dict):
            data = data.copy()
        else:
            data = {}

        if refresh_token:
            data['refresh'] = refresh_token

        serializer = self.get_serializer(data=data)

        try:
            serializer.is_valid(raise_exception=True)
        except (InvalidToken, TokenError) as e:
            response = Response({"detail": str(e)}, status=status.HTTP_401_UNAUTHORIZED)
            if refresh_token:
                response.delete_cookie(settings.JWT_COOKIE_NAME)
            return response

        token_data = serializer.validated_data
        response = Response(token_data, status=status.HTTP_200_OK)

        if response.status_code == status.HTTP_200_OK:
            if "refresh" in response.data:
                response.set_cookie(
                    key=settings.JWT_COOKIE_NAME,
                    value=response.data["refresh"],
                    httponly=settings.JWT_COOKIE_HTTP_ONLY,
                    secure=settings.JWT_COOKIE_SECURE,
                    samesite=settings.JWT_COOKIE_SAMESITE,
                    max_age=int(settings.SIMPLE_JWT['REFRESH_TOKEN_LIFETIME'].total_seconds()),
                )
                del response.data["refresh"]
        return response


class LogoutView(APIView):
    permission_classes = []

    def post(self, request, *args, **kwargs):
        response = Response({"detail": "Successfully logged out."}, status=status.HTTP_200_OK)
        response.delete_cookie(settings.JWT_COOKIE_NAME)
        return response
```

#### 4.15.6 `backend/plantilla_django_react/custom_auth/api/router.py` (CREAR)

Copia adaptada de yodumanager:1-21 (sin register y forgot-password):

```python
from django.conf import settings
from django.urls import path
from rest_framework.routers import DefaultRouter, SimpleRouter

from .viewsets.token import CustomTokenObtainPairView, CookieTokenRefreshView, LogoutView

router = DefaultRouter() if settings.DEBUG else SimpleRouter()

urlpatterns = [
    *router.urls,
    path("token", CustomTokenObtainPairView.as_view(), name="obtain_pair_token"),
    path("token/refresh", CookieTokenRefreshView.as_view(), name="refresh_token"),
    path("logout", LogoutView.as_view(), name="logout"),
]
```

#### 4.15.7 `backend/plantilla_django_react/custom_auth/api/viewsets/__init__.py` y `serializers/__init__.py` (CREAR)

Archivos vacíos.

#### 4.15.8 `backend/config/settings/base.py` (editar)

**a) Añadir a `THIRD_PARTY_APPS` (línea 74-86):**

```python
THIRD_PARTY_APPS = [
    # ... (existentes) ...
    "rest_framework_simplejwt",
    # ... (resto) ...
]
```

**b) Añadir a `LOCAL_APPS` (línea 88-91):**

```python
LOCAL_APPS = [
    "plantilla_django_react.users",
    "plantilla_django_react.custom_auth",
    # Your stuff: custom apps go here
]
```

**c) Cambiar `REST_FRAMEWORK` (líneas 332-339) — añadir `JWTAuthentication`:**

```python
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.TokenAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}
```

> **Por qué conservar `SessionAuthentication` y `TokenAuthentication`:**
> `SessionAuthentication` la usa el admin de Django (login tradicional).
> `TokenAuthentication` (`obtain_auth_token`) se conserva por si algún
> servicio interno la usa. **El nuevo flujo de auth es JWT, no Token.**

**d) Configurar `SIMPLE_JWT` y `JWT_COOKIE_*`** (al final del archivo, tras SPECTACULAR_SETTINGS):

```python
# django-rest-framework-simplejwt
# https://django-rest-framework-simplejwt.readthedocs.io/en/latest/settings.html
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=5),
    "REFRESH_TOKEN_LIFETIME": timedelta(
        days=env.int("JWT_REFRESH_TOKEN_DAYS", default=7),
    ),
    "ROTATE_REFRESH_TOKENS": False,
    "BLACKLIST_AFTER_ROTATION": False,
    "UPDATE_LAST_LOGIN": True,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": env("DJANGO_SECRET_KEY"),
    "AUTH_HEADER_TYPES": ("Bearer",),
    "AUTH_HEADER_NAME": "HTTP_AUTHORIZATION",
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
    "AUTH_TOKEN_CLASSES": ("rest_framework_simplejwt.tokens.AccessToken",),
    "TOKEN_TYPE_CLAIM": "token_type",
    "JTI_CLAIM": "jti",
}

# Custom Auth Cookie Settings
JWT_COOKIE_NAME = "refresh_token"
JWT_COOKIE_SECURE = env.bool("JWT_COOKIE_SECURE", default=False)
JWT_COOKIE_HTTP_ONLY = True
JWT_COOKIE_SAMESITE = "Lax"
```

**e) Importar `timedelta`** (al inicio, línea 4-7):

```python
from datetime import timedelta
```

#### 4.15.9 `backend/config/settings/local.py` (editar — añadir CORS credentials)

CORS_ALLOW_CREDENTIALS es necesario para que el navegador envíe la cookie
de refresh en cada request cross-origin. Sin esto, `credentials: 'include'`
en fetch falla con error CORS.

```python
# Añadir al final:
CORS_ALLOW_CREDENTIALS = True
```

> **Por qué se pone en `local.py` y no en `base.py`:** en `base.py` ya
> está `CORS_URLS_REGEX = r"^/api/.*$"` (línea 342). El flag
> `CORS_ALLOW_CREDENTIALS` es configuración de runtime (qué tan
> estricto es CORS), no de lógica. En `production.py` se deja el
> default `False` porque en producción con HTTPS y Same-Domain no se
> necesitan credenciales cross-origin (Dokploy + Traefik enrutan todo
> bajo el mismo dominio). Si el frontend se sirve en un subdominio
> distinto, se mueve también a `base.py`.

#### 4.15.10 `backend/config/urls.py` (editar — añadir rutas de auth)

**a) Cambiar la línea 75 (eliminar `obtain_auth_token`):**

```python
# Reemplazar:
# path("api/auth-token/", obtain_auth_token, name="obtain_auth_token"),
# Por:
# (eliminar el import también: from rest_framework.authtoken.views import obtain_auth_token)
```

**b) Añadir el include del router de `custom_auth`:**

Tras `path("api/", include("config.api_router"))` (línea 73), añadir:

```python
path("api/auth/", include("plantilla_django_react.custom_auth.api.router")),
```

**Resultado de endpoints:**
- `POST /api/auth/token` → login (devuelve `{access, user}` + cookie)
- `POST /api/auth/token/refresh` → refresh (lee cookie, devuelve `{access}`)
- `POST /api/auth/logout` → logout (borra cookie)
- `GET /api/users/me/` → ya existe (UserViewSet, action `me`)

#### 4.15.11 `backend/.envs/.local/.django` y `.production/.django` (editar)

Añadir `JWT_REFRESH_TOKEN_DAYS` y `JWT_COOKIE_SECURE`:

```ini
# En .envs/.local/.django añadir:
JWT_REFRESH_TOKEN_DAYS=7
JWT_COOKIE_SECURE=false
```

```ini
# En .envs/.production/.django añadir:
JWT_REFRESH_TOKEN_DAYS=7
JWT_COOKIE_SECURE=true
```

> **Decisión sobre `JWT_COOKIE_SECURE`:** en local `false` (HTTP),
> en producción `true` (HTTPS via Dokploy Traefik). El navegador no
> envía cookies `Secure` sobre HTTP.

### 4.16 Frontend: Servicio API base con fetch + auto-refresh (CREAR)

#### 4.16.1 `frontend/package.json` (editar — quitar axios, añadir zustand)

`axios` **se elimina** (no se usará). `zustand` se añade para el authStore
(patrón de yodumanager, mismo paquete).

```json
"dependencies": {
  "react": "^19.2.6",
  "react-dom": "^19.2.6",
  "zustand": "^5.0.0"
}
```

> **Decisión:** zustand sí, axios no. Zustand es necesario para el
> store con `persist` (igual que yodumanager). Si se prefiere cero
> deps para el estado, se puede usar `useSyncExternalStore` nativo
> de React 18+ (más boilerplate, sin devtools).
>
> **Versiones:** `zustand@^5.0.0` es la estable a la fecha.

#### 4.16.2 `frontend/src/api/apiClient.js` (CREAR — servicio base reusable)

**Diseñado como librería genérica, sin dependencias del proyecto.** Los
endpoints y el callback de logout se inyectan en la inicialización
(patrón Singleton + Dependency Injection).

```js
/**
 * apiClient.js — Servicio HTTP base con auto-refresh JWT.
 *
 * Diseñado para ser reusable entre proyectos:
 * - Sin dependencias externas (usa fetch nativo).
 * - Sin acoplarse a un store específico (usa callbacks inyectables).
 * - Configurable via setConfig().
 *
 * Uso:
 *   import { api, setConfig } from '@/api/apiClient'
 *
 *   setConfig({
 *     baseURL: import.meta.env.VITE_API_URL,
 *     refreshEndpoint: '/api/auth/token/refresh',
 *     loginEndpoint: '/api/auth/token',
 *     onUnauthorized: () => { /* logout, redirect *\/ },
 *     onTokenChange: (access) => { /* persist *\/ },
 *     onError: (err) => { /* toast *\/ },
 *   })
 *
 *   await api.post('/api/users/', { name: 'foo' })
 *   await api.get('/api/users/me/')
 *
 * Convenciones:
 * - El access token se guarda en memoria del módulo (no en localStorage)
 *   y se persiste via onTokenChange.
 * - El refresh token viaja en cookie HttpOnly (no accesible desde JS).
 * - Las requests con cookies usan credentials: 'include'.
 * - En un 401 (no siendo login/refresh), se intenta refresh una vez
 *   y se reintenta la request original. Si el refresh falla, se
 *   llama a onUnauthorized.
 */

let config = {
  baseURL: '',
  refreshEndpoint: '/api/auth/token/refresh',
  loginEndpoint: '/api/auth/token',
  onUnauthorized: null,
  onTokenChange: null,
  onError: null,
}

let accessToken = null
let isRefreshing = false
let failedQueue = []

export function setConfig(partial) {
  config = { ...config, ...partial }
}

export function setAccessToken(token) {
  accessToken = token
  if (config.onTokenChange) config.onTokenChange(token)
}

export function clearAccessToken() {
  accessToken = null
  if (config.onTokenChange) config.onTokenChange(null)
}

function buildUrl(path) {
  if (path.startsWith('http')) return path
  return `${config.baseURL}${path}`
}

async function parseResponse(response) {
  const text = await response.text()
  const data = text ? JSON.parse(text) : null
  if (!response.ok) {
    const error = new Error(data?.detail || response.statusText)
    error.status = response.status
    error.data = data
    throw error
  }
  return data
}

function processQueue(error, token = null) {
  failedQueue.forEach(({ resolve, reject }) => {
    if (error) reject(error)
    else resolve(token)
  })
  failedQueue = []
}

async function doFetch(path, options = {}) {
  const { skipAuth = false, ...fetchOptions } = options

  const headers = { ...fetchOptions.headers }
  if (fetchOptions.body && !(fetchOptions.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json'
    if (typeof fetchOptions.body !== 'string') {
      fetchOptions.body = JSON.stringify(fetchOptions.body)
    }
  }
  if (!skipAuth && accessToken) {
    headers['Authorization'] = `Bearer ${accessToken}`
  }

  return fetch(buildUrl(path), {
    ...fetchOptions,
    headers,
    credentials: 'include', // siempre: cookie de refresh HttpOnly
  })
}

async function refreshAccessToken() {
  // Usa fetch crudo (sin auto-refresh) para evitar loop infinito.
  const response = await fetch(buildUrl(config.refreshEndpoint), {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
  })
  if (!response.ok) {
    throw new Error('Refresh failed')
  }
  const data = await response.json()
  if (data.access) {
    setAccessToken(data.access)
    return data.access
  }
  throw new Error('No access in refresh response')
}

async function request(path, options = {}) {
  let response
  try {
    response = await doFetch(path, options)
  } catch (err) {
    if (config.onError) config.onError(err)
    throw err
  }

  // Auto-refresh on 401 (excepto login, refresh, verify, logout).
  const skipRefresh =
    path.includes(config.loginEndpoint) ||
    path.includes(config.refreshEndpoint) ||
    path.includes('/logout')

  if (response.status === 401 && !options._retry && !skipRefresh) {
    if (isRefreshing) {
      // Encolar: esperar a que el refresh actual termine.
      return new Promise((resolve, reject) => {
        failedQueue.push({
          resolve: (newToken) => {
            options.headers = { ...(options.headers || {}), Authorization: `Bearer ${newToken}` }
            request(path, { ...options, _retry: true }).then(resolve, reject)
          },
          reject,
        })
      })
    }

    options._retry = true
    isRefreshing = true
    try {
      const newToken = await refreshAccessToken()
      processQueue(null, newToken)
      // Reintentar el request original con el nuevo token.
      options.headers = { ...(options.headers || {}), Authorization: `Bearer ${newToken}` }
      return request(path, options)
    } catch (refreshError) {
      processQueue(refreshError, null)
      clearAccessToken()
      if (config.onUnauthorized) config.onUnauthorized(refreshError)
      throw refreshError
    } finally {
      isRefreshing = false
    }
  }

  return parseResponse(response)
}

export const api = {
  get:    (path, options)         => request(path, { ...options, method: 'GET' }),
  post:   (path, body, options)   => request(path, { ...options, method: 'POST', body }),
  put:    (path, body, options)   => request(path, { ...options, method: 'PUT', body }),
  patch:  (path, body, options)   => request(path, { ...options, method: 'PATCH', body }),
  delete: (path, options)         => request(path, { ...options, method: 'DELETE' }),
}

export default api
```

**Notas de diseño:**
- `setConfig()` permite inyectar comportamiento del proyecto (base URL,
  callbacks) sin acoplar el módulo a nada concreto.
- `setAccessToken` / `clearAccessToken` permiten al `authStore` persistir
  el token en `localStorage` y rehidratar al cargar.
- El 401→refresh→retry es 100% transparente para el consumidor: `await
  api.get('/foo')` siempre devuelve la respuesta correcta, o lanza el
  error final.
- `failedQueue` usa la misma técnica que yodumanager: cada elemento es
  `{resolve, reject}`. Si el refresh funciona, se resuelven todas con el
  nuevo token. Si falla, se rechazan todas.

#### 4.16.3 `frontend/src/api/apiEndpoints.js` (CREAR)

Centraliza todas las URLs de la API. Inspirado en yodumanager:1-106.

```js
const API_ENDPOINTS = {
  auth: {
    login: '/api/auth/token',
    refresh: '/api/auth/token/refresh',
    logout: '/api/auth/logout',
  },
  user: {
    me: '/api/users/me/',
  },
}

export default API_ENDPOINTS
```

> **Por qué centralizar:** cambiar una URL es 1 línea. Si se añade un
> nuevo endpoint (categorías, gastos, etc.), se añade aquí. Patrón
> usado en yodumanager y en prácticamente todas las apps de producción.

#### 4.16.4 `frontend/src/store/authStore.js` (CREAR)

Copia del patrón de yodumanager:1-45 (Zustand + persist).

```js
import { create } from 'zustand'
import { persist } from 'zustand/middleware'

/**
 * Auth store. Solo persiste el access token y el user.
 * El refresh token NO se persiste aquí — vive en cookie HttpOnly.
 */
export const useAuthStore = create()(
  persist(
    (set) => ({
      user: null,
      access: null,
      isAuthenticated: false,

      login: (userData, accessToken) =>
        set({
          user: userData,
          access: accessToken,
          isAuthenticated: true,
        }),

      logout: () =>
        set({
          user: null,
          access: null,
          isAuthenticated: false,
        }),

      setAccess: (accessToken) => set({ access: accessToken }),

      setUser: (userData) => set({ user: userData }),
    }),
    {
      name: 'auth-settings',
      partialize: (state) => ({
        user: state.user,
        access: state.access,
        isAuthenticated: state.isAuthenticated,
      }),
    }
  )
)
```

#### 4.16.5 `frontend/src/api/authService.js` (CREAR)

Capa fina sobre `api` con funciones semánticas. Inyecta config al `apiClient`
y conecta con el `authStore`.

```js
import { api, setConfig, setAccessToken, clearAccessToken } from './apiClient'
import { useAuthStore } from '@/store/authStore'
import API_ENDPOINTS from './apiEndpoints'

/**
 * Inicializa el apiClient con la config del proyecto.
 * Llamar UNA vez en el entry point (main.jsx) antes de cualquier request.
 */
export function initApiClient() {
  setConfig({
    baseURL: import.meta.env.VITE_API_URL,
    refreshEndpoint: API_ENDPOINTS.auth.refresh,
    loginEndpoint: API_ENDPOINTS.auth.login,
    onUnauthorized: () => {
      useAuthStore.getState().logout()
    },
    onTokenChange: (access) => {
      if (access) useAuthStore.getState().setAccess(access)
    },
  })

  // Rehidratar access token desde el store persistido (si existe).
  const persisted = useAuthStore.getState().access
  if (persisted) setAccessToken(persisted)
}

/**
 * Login: POST /api/auth/token
 * Devuelve { access, user } y guarda en el store.
 * El refresh token se setea automáticamente como cookie HttpOnly.
 */
export async function login(username, password) {
  const data = await api.post(API_ENDPOINTS.auth.login, { username, password })
  // data = { access, user } (el refresh ya está en cookie)
  useAuthStore.getState().login(data.user, data.access)
  setAccessToken(data.access)
  return data
}

/**
 * Logout: borra cookie en el server + limpia store local.
 */
export async function logout() {
  try {
    await api.post(API_ENDPOINTS.auth.logout, {})
  } catch {
    // Ignorar error: igual limpiamos el store local
  } finally {
    useAuthStore.getState().logout()
    clearAccessToken()
  }
}

/**
 * Get current user: GET /api/users/me/
 */
export async function getMe() {
  const data = await api.get(API_ENDPOINTS.user.me)
  useAuthStore.getState().setUser(data)
  return data
}
```

#### 4.16.6 `frontend/src/main.jsx` (editar — llamar `initApiClient`)

```jsx
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import { initApiClient } from './api/authService'

// Inicializa el apiClient (lee env, conecta con authStore) antes de montar React.
initApiClient()

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
```

> **Por qué antes del render:** si la app rehidrata con un access token
> expirado, el primer `useEffect` disparará un refresh transparente
> antes de mostrar contenido. Sin `initApiClient` antes del render, la
> primera request puede salir con token vacío.

#### 4.16.7 `frontend/src/pages/LoginPage.jsx` (CREAR)

Formulario controlado con email + password, manejo de loading y error.

```jsx
import { useState } from 'react'
import { login } from '@/api/authService'

export default function LoginPage({ onLogin }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  async function handleSubmit(e) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      const data = await login(email, password)
      onLogin?.(data.user)
    } catch (err) {
      setError(
        err.status === 401
          ? 'Credenciales inválidas'
          : err.status === 400
            ? 'Faltan datos (email y password requeridos)'
            : `Error ${err.status || ''}: ${err.message}`
      )
    } finally {
      setLoading(false)
    }
  }

  return (
    <main style={{ maxWidth: 360, margin: '64px auto', padding: 24 }}>
      <h1>Login</h1>
      <p style={{ color: '#666' }}>
        Prueba de CORS + DRF SimpleJWT. Inicia sesión con un superusuario.
      </p>
      <form onSubmit={handleSubmit}>
        <label style={{ display: 'block', marginTop: 12 }}>
          Email
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            autoComplete="username"
            style={{ width: '100%', padding: 8, marginTop: 4 }}
          />
        </label>
        <label style={{ display: 'block', marginTop: 12 }}>
          Password
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            autoComplete="current-password"
            style={{ width: '100%', padding: 8, marginTop: 4 }}
          />
        </label>
        {error && (
          <p style={{ color: 'crimson', marginTop: 12 }}>{error}</p>
        )}
        <button
          type="submit"
          disabled={loading}
          style={{ marginTop: 16, padding: '8px 16px', width: '100%' }}
        >
          {loading ? 'Entrando…' : 'Entrar'}
        </button>
      </form>
    </main>
  )
}
```

> **Nota sobre `username` vs `email`:** SimpleJWT por defecto acepta
> `username` en el body. La plantilla usa `email` como
> `USERNAME_FIELD` (django-allauth). El serializer de SimpleJWT es
> "tonto" y acepta el campo que el User diga, así que enviar
> `{email, password}` debería funcionar. Si no funciona, enviar
> `{username: email, password}` y listo.

#### 4.16.8 `frontend/src/App.jsx` (editar — reemplazar placeholder)

```jsx
import { useState, useEffect } from 'react'
import LoginPage from '@/pages/LoginPage'
import { useAuthStore } from '@/store/authStore'
import { getMe, logout } from '@/api/authService'
import './App.css'

function App() {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  const user = useAuthStore((s) => s.user)
  const [me, setMe] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!isAuthenticated) return
    getMe()
      .then((data) => setMe(data))
      .catch((err) => setError(err.message))
  }, [isAuthenticated])

  if (!isAuthenticated) return <LoginPage />

  async function handleLogout() {
    await logout()
  }

  return (
    <main style={{ padding: 24 }}>
      <h1>CORS OK ✅ — SimpleJWT funcionando</h1>
      <p>
        Bienvenido <strong>{user?.email ?? '...'}</strong>
      </p>
      {error && <p style={{ color: 'crimson' }}>Error: {error}</p>}
      <details>
        <summary>Datos de /api/users/me/ (válida CORS + Authorization)</summary>
        <pre>{JSON.stringify(me, null, 2)}</pre>
      </details>
      <button onClick={handleLogout} style={{ marginTop: 16 }}>
        Cerrar sesión
      </button>
    </main>
  )
}

export default App
```

#### 4.16.9 `frontend/vite.config.js` (editar — alias `@`)

El `apiService.js` y `App.jsx` usan `@/...` para los imports. Añadir
el alias (si no estaba):

```js
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')

  return {
    plugins: [react()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    server: {
      host: '0.0.0.0',
      port: 5173,
      watch: { usePolling: true, interval: 300 },
    },
  }
})
```

> **Alias `@`:** patrón estándar para imports limpios en proyectos Vite
> medianos. yodumanager lo trae en su `vite.config.js:51-53`.

#### 4.16.10 Eliminar el placeholder de `App.css` (opcional)

`App.jsx` ya no usa `icons.svg`. Se puede dejar `App.css` tal cual
(afecta solo estilos del placeholder).

### 4.17 Validación end-to-end con curl + Playwright MCP

Una vez levantado el stack (Paso 10), se valida la integración completa
en dos fases: **backend con curl** (rápido, determinista) y **frontend
con Playwright MCP** (visual, valida CORS en navegador real). Ambas
combinadas garantizan que el flujo SimpleJWT + cookie HttpOnly + CORS
credentials funciona end-to-end.

#### 4.17.1 Pruebas backend con curl (en host)

**Setup previo:**

```bash
# 1. Crear superusuario (bypass pgbouncer, ya que migrate directo)
just manage-direct-db createsuperuser

# 2. Login que guarda cookie jar y captura access token
curl -s -c /tmp/jar.txt -X POST http://localhost:8000/api/auth/token \
  -H "Content-Type: application/json" \
  -d '{"username":"admin@example.com","password":"changeme"}' \
  -o /tmp/login.json
ACCESS=$(jq -r '.access' /tmp/login.json)
echo "Access: $ACCESS"
```

**Tabla de casos de prueba (7 + 1 preflight):**

| # | Comando | Esperado |
|---|---|---|
| 1 | `curl -i -X POST http://localhost:8000/api/auth/token -H "Content-Type: application/json" -d '{}'` | **400** con detalle de campos faltantes |
| 2 | `curl -i -X POST http://localhost:8000/api/auth/token -H "Content-Type: application/json" -d '{"username":"admin@example.com","password":"changeme"}'` | **200** + `Set-Cookie: refresh_token=...; HttpOnly; SameSite=Lax` + body `{access, user}` |
| 3 | `curl -X POST http://localhost:8000/api/auth/token/refresh -b /tmp/jar.txt` | **200** con `{access}` nuevo + `Set-Cookie` actualizada |
| 4 | `curl http://localhost:8000/api/users/me/ -H "Authorization: Bearer $ACCESS"` | **200** con JSON del user (id, email, username, is_staff, is_superuser) |
| 5 | `curl -i http://localhost:8000/api/users/me/` (sin `Authorization`) | **401** con `{"detail":"Authentication credentials were not provided."}` |
| 6 | `curl -i -X POST http://localhost:8000/api/auth/token -H "Origin: http://localhost:5173" -H "Content-Type: application/json" -d '{}'` | response headers incluyen `Access-Control-Allow-Origin: http://localhost:5173` y `Access-Control-Allow-Credentials: true` |
| 7 | `curl -i -X POST http://localhost:8000/api/auth/logout -b /tmp/jar.txt` | **200** + `Set-Cookie: refresh_token=; expires=<pasado>; Max-Age=0` |
| 8 | `curl -i -X OPTIONS http://localhost:8000/api/users/me/ -H "Origin: http://localhost:5173" -H "Access-Control-Request-Method: GET" -H "Access-Control-Request-Headers: authorization,content-type"` | **200** con headers: `Access-Control-Allow-Origin`, `Access-Control-Allow-Methods` (incluye GET), `Access-Control-Allow-Headers` (incluye authorization), `Access-Control-Allow-Credentials: true` |

**Si algún caso falla, los logs ayudan:**

```bash
docker compose -f docker-compose.local.yml logs django --tail=30 | grep -E 'CORS|Origin|cookie'
docker compose -f docker-compose.local.yml logs pgbouncer --tail=20
```

#### 4.17.2 Pruebas frontend con Playwright MCP (en navegador real)

Usar el servidor MCP de Playwright (expuesto en opencode) para
automatizar Chrome/Chromium y validar el flujo visual + cookies + CORS
+ auto-refresh. El agente ejecuta los pasos en orden:

**Fase A — Login y dashboard:**

1. `playwright_browser_navigate({url: 'http://localhost:5173/'})`
2. `playwright_browser_wait_for({text: 'Login'})` — confirma que carga el `LoginPage`.
3. `playwright_browser_snapshot()` — verificar `<form>` con inputs `email` + `password` y botón "Entrar".
4. `playwright_browser_fill_form({fields: [{name: 'email', type: 'textbox', value: 'admin@example.com', target: 'input[type=email]'}, {name: 'password', type: 'textbox', value: 'changeme', target: 'input[type=password]'}]})`.
5. `playwright_browser_click({element: 'botón Entrar', target: 'button[type=submit]'})`.
6. `playwright_browser_wait_for({text: 'CORS OK'})` — confirma que el login redirige al dashboard.
7. `playwright_browser_snapshot()` — leer el header "CORS OK ✅ — SimpleJWT funcionando" y el JSON del usuario en `<details>`.
8. `playwright_browser_take_screenshot({type: 'png', filename: 'plan-validacion-dashboard.png', fullPage: true})` — evidencia visual.

**Fase B — Verificar cookies + tokens (aserciones críticas):**

9. `playwright_browser_evaluate({function: '() => ({ hasRefreshCookie: document.cookie.includes("refresh_token"), accessPersisted: !!localStorage.getItem("auth-settings"), bodyText: document.body.innerText.substring(0, 200) })'})`
   - **Esperado:** `hasRefreshCookie: false` (la cookie es HttpOnly, no accesible por JS — esto valida la seguridad XSS), `accessPersisted: true`.
10. `playwright_browser_evaluate({function: '() => { const raw = localStorage.getItem("auth-settings"); return raw ? JSON.parse(raw) : null }'})` — leer el store persistido; debe contener `state.access` y `state.user`.
11. `playwright_browser_network_requests({static: false, filter: '/api/'})` — revisar:
    - `POST /api/auth/token` → 200, response headers: `access-control-allow-origin: http://localhost:5173` + `access-control-allow-credentials: true` + `set-cookie: refresh_token=...`.
    - `GET /api/users/me/` → 200, request headers: `authorization: Bearer <access>`.
12. `playwright_browser_console_messages({level: 'warning'})` — no debe haber warnings de CORS (Access-Control-Allow-Origin, credentials, etc.).

**Fase C — Probar auto-refresh (la parte más importante):**

13. **Acelerar el ciclo:** editar temporalmente `backend/config/settings/base.py` → `ACCESS_TOKEN_LIFETIME = timedelta(seconds=15)` (15s en vez de 5min). Reiniciar `docker compose -f docker-compose.local.yml restart django`.
14. Esperar ~20s para que el access actual expire.
15. `playwright_browser_evaluate({function: 'async () => { const r = await fetch("http://localhost:8000/api/users/me/", { credentials: "include" }); return { status: r.status, hasAuth: r.headers.get("www-authenticate") } }'})` → debe devolver `{status: 200}` (el `apiClient` interceptó el 401, llamó a `refresh`, reintentó y devolvió 200).
16. `playwright_browser_network_requests({static: false, filter: '/api/auth/token/refresh'})` → debe haber al menos 1 request a refresh con status 200.
17. **Revertir el cambio** del paso 13.

**Fase D — Probar logout:**

18. `playwright_browser_click({element: 'botón Cerrar sesión', target: 'button:has-text("Cerrar sesión")'})`.
19. `playwright_browser_wait_for({text: 'Login'})` — vuelve al LoginPage.
20. `playwright_browser_evaluate({function: '() => ({ cookies: document.cookie, lsAuth: localStorage.getItem("auth-settings") })'})` → `cookies` no debe contener `refresh_token`; `lsAuth` debe ser `null` o sin `state.access`.
21. `playwright_browser_network_requests({static: false, filter: '/api/auth/logout'})` → 1 request a logout con status 200 + `set-cookie: refresh_token=; expires=...`.

**Fase E — Capturar evidencia final:**

22. `playwright_browser_take_screenshot({type: 'png', filename: 'plan-validacion-logout.png', fullPage: true})`.
23. `playwright_browser_close()`.

#### 4.17.3 Salida esperada (resumen)

Si todos los pasos pasan, el plan está validado:

- ✅ Backend responde con JWT + cookie HttpOnly
- ✅ CORS preflight (OPTIONS) permite credenciales desde `localhost:5173`
- ✅ Frontend recibe el token, lo guarda en `localStorage`, persiste user en store
- ✅ Cookie de refresh NO es accesible por JS (XSS-resistant)
- ✅ `GET /api/users/me/` envía `Authorization: Bearer ...` y devuelve 200
- ✅ Auto-refresh transparente: tras 401, dispara `POST /api/auth/token/refresh` y reintenta sin que el código de la app lo note
- ✅ Logout limpia cookie en el server y store local

#### 4.17.4 Tests E2E persistentes (fuera de scope)

Si se quieren tests Playwright persistentes en el repo (no manuales
vía MCP), crear `frontend/tests/e2e/login.spec.js` y añadir
`@playwright/test` como devDep. **Fuera de scope** de este plan — los
tests E2E automatizados se pueden añadir en un plan separado.

---

## 5. Archivos a crear / editar (resumen)

### Crear (20 archivos)

| Ruta | Origen adaptado de |
| --- | --- |
| `docker-compose.dokploy.yml` | `yodumanager-v2/docker-compose.dokploy.yml` |
| `frontend/compose/local/Dockerfile` | `yodumanager-v2/frontend/compose/local/Dockerfile` |
| `frontend/compose/production/Dockerfile` | `yodumanager-v2/frontend/compose/production/Dockerfile` |
| `frontend/compose/production/nginx/nginx.conf` | `yodumanager-v2/frontend/compose/production/nginx/nginx.conf` |
| `frontend/start-frontend` | `yodumanager-v2/frontend/start-frontend` |
| `frontend/.dockerignore` | `yodumanager-v2/frontend/.dockerignore` |
| `frontend/.env` | `yodumanager-v2/frontend/.env` |
| `backend/compose/production/pgbouncer/Dockerfile` | `yodumanager-v2/backend/compose/production/pgbouncer/Dockerfile` |
| `backend/compose/production/pgbouncer/entrypoint.sh` | `yodumanager-v2/backend/compose/production/pgbouncer/entrypoint.sh` |
| `backend/compose/production/pgbouncer/pgbouncer.ini.template` | `yodumanager-v2/backend/compose/production/pgbouncer/pgbouncer.ini.template` |
| `backend/compose/production/pgbouncer/userlist.template.txt` | `yodumanager-v2/backend/compose/production/pgbouncer/userlist.template.txt` |
| `backend/plantilla_django_react/custom_auth/__init__.py` | nuevo (app vacía) |
| `backend/plantilla_django_react/custom_auth/apps.py` | nuevo (config de app) |
| `backend/plantilla_django_react/custom_auth/api/__init__.py` | nuevo |
| `backend/plantilla_django_react/custom_auth/api/router.py` | `yodumanager-v2/backend/apps/custom_auth/api/router.py` |
| `backend/plantilla_django_react/custom_auth/api/serializers/__init__.py` | nuevo |
| `backend/plantilla_django_react/custom_auth/api/serializers/token.py` | `yodumanager-v2/backend/apps/custom_auth/api/serializers/token.py` (adaptado) |
| `backend/plantilla_django_react/custom_auth/api/viewsets/__init__.py` | nuevo |
| `backend/plantilla_django_react/custom_auth/api/viewsets/token.py` | `yodumanager-v2/backend/apps/custom_auth/api/viewsets/token.py` |
| `frontend/src/api/apiClient.js` | **NUEVO** — fetch + auto-refresh (reusable) |
| `frontend/src/api/apiEndpoints.js` | nuevo (constantes de URLs) |
| `frontend/src/api/authService.js` | nuevo (init + login + logout + me) |
| `frontend/src/store/authStore.js` | `yodumanager-v2/frontend/src/store/authStore.js` |
| `frontend/src/pages/LoginPage.jsx` | nuevo |
| `START.md` | **NUEVO** — guía de rename de la plantilla (ver sección 4.18) |

### Editar (22 archivos)

| Ruta | Cambio |
| --- | --- |
| `docker-compose.local.yml` | añadir pgbouncer + cambiar depends_on + añadir frontend |
| `docker-compose.production.yml` | añadir pgbouncer + cambiar depends_on + añadir frontend |
| `backend/pyproject.toml` | añadir `djangorestframework-simplejwt==5.5.1` |
| `backend/uv.lock` | regenerar tras `uv lock` |
| `backend/.envs/.local/.django` | añadir CORS / CSRF / LOG_FILE / DATABASE_URL→pgbouncer / JWT_REFRESH_TOKEN_DAYS / JWT_COOKIE_SECURE |
| `backend/.envs/.local/.postgres` | añadir bloque `PGB_*` |
| `backend/.envs/.production/.django` | añadir CORS / CSRF / LOG_FILE / DATABASE_URL→pgbouncer / JWT_REFRESH_TOKEN_DAYS / JWT_COOKIE_SECURE=true |
| `backend/.envs/.production/.postgres` | añadir bloque `PGB_*` |
| `backend/compose/local/django/Dockerfile` | añadir `mkdir -p /app/logs` |
| `backend/compose/production/django/Dockerfile` | añadir `mkdir -p /app/logs` |
| `backend/compose/production/django/entrypoint` | usar `PGB_POSTGRES_*` y `wait-for-it pgbouncer` |
| `backend/compose/production/django/celery/worker/start` | añadir `mkdir -p /app/logs` |
| `backend/compose/production/django/celery/beat/start` | añadir `mkdir -p /app/logs` |
| `backend/compose/production/django/celery/flower/start` | añadir `mkdir -p /app/logs` |
| `backend/justfile` | añadir `manage-direct-db` y `pytest` con override de pgbouncer |
| `backend/config/settings/base.py` | añadir `rest_framework_simplejwt` a INSTALLED_APPS / SIMPLE_JWT config / JWT_COOKIE_* / añadir JWTAuthentication en REST_FRAMEWORK / `from datetime import timedelta` |
| `backend/config/settings/local.py` | añadir `CORS_ALLOW_CREDENTIALS = True` |
| `backend/config/urls.py` | eliminar `obtain_auth_token`, añadir include de custom_auth router |
| `frontend/vite.config.js` | añadir `host 0.0.0.0`, `usePolling`, alias `@` |
| `frontend/src/main.jsx` | añadir `initApiClient()` antes del render |
| `frontend/src/App.jsx` | reemplazar placeholder por LoginPage + protected route simple |
| `frontend/package.json` | **quitar `axios`**, añadir `zustand` |

---

## 6. Pasos de ejecución (en orden)

> Cada paso tiene un comando de verificación independiente. Si algo
> falla, no se sigue.

### Paso 1 — Editar envs de backend (CORS/CSRF/LOG/JWT/pgbouncer)
- Editar `backend/.envs/.local/.django` y `.envs/.production/.django` (CORS, CSRF, LOG_FILE, DATABASE_URL, JWT).
- Editar `backend/.envs/.local/.postgres` y `.envs/.production/.postgres` (bloque `PGB_*`).
- **Verificar:** `grep -E 'CORS|CSRF|NAME_LOG|DATABASE_URL|JWT_|PGB_' backend/.envs/.local/.django backend/.envs/.local/.postgres`

### Paso 2 — Backend: añadir `simplejwt` y crear app `custom_auth`
- Editar `backend/pyproject.toml` (añadir `djangorestframework-simplejwt==5.5.1`).
- Ejecutar `uv lock --upgrade` (regenera `uv.lock`).
- Crear `backend/plantilla_django_react/custom_auth/` con su `apps.py` y los 3 `__init__.py`.
- Crear `backend/plantilla_django_react/custom_auth/api/serializers/token.py` y `viewsets/token.py` y `router.py`.
- **Verificar:** `ls -R backend/plantilla_django_react/custom_auth/`

### Paso 3 — Backend: configurar settings + urls
- Editar `backend/config/settings/base.py` (TIMESTAMP → `from datetime import timedelta`; añadir `rest_framework_simplejwt` a THIRD_PARTY_APPS; añadir `plantilla_django_react.custom_auth` a LOCAL_APPS; añadir JWTAuthentication en REST_FRAMEWORK; añadir bloque `SIMPLE_JWT` y `JWT_COOKIE_*`).
- Editar `backend/config/settings/local.py` (añadir `CORS_ALLOW_CREDENTIALS = True`).
- Editar `backend/config/urls.py` (eliminar `obtain_auth_token`, añadir include de custom_auth).
- **Verificar:** `python -c "from django.conf import settings; print(settings.SIMPLE_JWT['ACCESS_TOKEN_LIFETIME'])"` debe imprimir `0:05:00`.

### Paso 4 — Crear estructura `frontend/compose/`
- Crear `frontend/compose/local/Dockerfile`
- Crear `frontend/compose/production/Dockerfile`
- Crear `frontend/compose/production/nginx/nginx.conf`
- Crear `frontend/start-frontend` (`chmod +x`)
- Crear `frontend/.dockerignore`
- Crear `frontend/.env`
- **Verificar:** `ls -R frontend/compose frontend/start-frontend frontend/.env frontend/.dockerignore`

### Paso 5 — Crear estructura `backend/compose/production/pgbouncer/`
- Crear los 4 archivos (Dockerfile, entrypoint.sh, pgbouncer.ini.template, userlist.template.txt).
- **Verificar:** `ls backend/compose/production/pgbouncer/`

### Paso 6 — Editar compose de backend
- `backend/compose/local/django/Dockerfile`: añadir `mkdir -p ${APP_HOME}/logs`
- `backend/compose/production/django/Dockerfile`: añadir `mkdir -p ${APP_HOME}/logs` junto al de media
- `backend/compose/production/django/entrypoint`: usar `PGB_POSTGRES_*` y `wait-for-it pgbouncer`
- `backend/compose/production/django/celery/{worker,beat,flower}/start`: añadir `mkdir -p /app/logs`
- **Verificar:** `grep -n 'logs' backend/compose/local/django/Dockerfile backend/compose/production/django/Dockerfile`

### Paso 7 — Añadir servicios a los dos compose existentes
- Editar `docker-compose.local.yml` (insertar pgbouncer, cambiar depends_on, añadir frontend).
- Editar `docker-compose.production.yml` (insertar pgbouncer, cambiar depends_on, añadir frontend).
- **Verificar:** `grep -A2 'frontend:\|pgbouncer:' docker-compose.local.yml docker-compose.production.yml`

### Paso 7b — Actualizar `backend/justfile` con `manage-direct-db` y `pytest`
- Añadir las recetas `manage-direct-db` y `pytest` de yodumanager:40-50.
- **Verificar:** `just --list` debe mostrar `manage-direct-db` y `pytest`.

### Paso 8 — Crear `docker-compose.dokploy.yml`
- Crear el archivo nuevo en la raíz.
- **Verificar:** `docker compose -f docker-compose.dokploy.yml config --services` debe listar `django`, `pgbouncer`, `celeryworker`, `celerybeat`, `flower`, `nginx`, `frontend`.

### Paso 9 — Frontend: crear servicio base + auth
- `pnpm remove axios` (si estaba), `pnpm add zustand`.
- Crear `frontend/src/api/apiClient.js` (servicio fetch reusable con auto-refresh).
- Crear `frontend/src/api/apiEndpoints.js` (constantes).
- Crear `frontend/src/store/authStore.js` (Zustand con persist).
- Crear `frontend/src/api/authService.js` (initApiClient + login + logout + getMe).
- Editar `frontend/vite.config.js` (host 0.0.0.0, usePolling, alias `@`).
- Editar `frontend/src/main.jsx` (llamar `initApiClient()` antes del render).
- Crear `frontend/src/pages/LoginPage.jsx`.
- Editar `frontend/src/App.jsx` (reemplazar placeholder por LoginPage + protected view).
- **Verificar:** `grep -r 'axios' frontend/package.json frontend/src/` debe devolver vacío.

### Paso 10 — Levantar el stack local
- Crear superusuario vía `just manage-direct-db createsuperuser` (bypass pgbouncer)
- `docker compose -f docker-compose.local.yml up -d --build`
- **Verificar:** `docker compose -f docker-compose.local.yml ps` (todos `Up`)
- **Verificar:** `docker compose -f docker-compose.local.yml exec pgbouncer psql -h postgres -U debug -d plantilla_django_react -c '\dt'` (conexión real postgres funciona)
- **Verificar:** `docker compose -f docker-compose.local.yml exec django python -c "import django; django.setup(); from django.db import connection; connection.ensure_connection(); print('OK')"` (Django conecta vía pgbouncer)
- **Verificar:** `docker compose -f docker-compose.local.yml logs celeryworker | grep -i 'ready\|error' | head -5`

### Paso 11 — Validar backend con curl (8 casos de prueba)

Ejecutar la tabla de curl de la sección 4.17.1. Resumen:

```bash
# Setup
just manage-direct-db createsuperuser
curl -s -c /tmp/jar.txt -X POST http://localhost:8000/api/auth/token \
  -H "Content-Type: application/json" \
  -d '{"username":"admin@example.com","password":"changeme"}' \
  -o /tmp/login.json
ACCESS=$(jq -r '.access' /tmp/login.json)
```

| # | Caso | Esperado |
|---|------|----------|
| 1 | POST token body vacío | 400 con detalle |
| 2 | POST token credenciales válidas | 200 + Set-Cookie HttpOnly + body {access,user} |
| 3 | POST refresh con cookie | 200 con {access} nuevo |
| 4 | GET me con Bearer | 200 con user JSON |
| 5 | GET me sin Bearer | 401 |
| 6 | POST token con Origin header | CORS headers presentes |
| 7 | POST logout con cookie | 200 + Set-Cookie expirada |
| 8 | OPTIONS preflight a /me | 200 con Access-Control-Allow-* |

**Si algún caso falla:** revisar `docker compose -f docker-compose.local.yml logs django --tail=50`.

### Paso 11b — Validar frontend con Playwright MCP (5 fases)

Ejecutar las 5 fases de la sección 4.17.2. Resumen:
- **Fase A** (1-8): login + dashboard visible.
- **Fase B** (9-12): verificar cookie HttpOnly (no en `document.cookie`), access persistido en localStorage, headers CORS.
- **Fase C** (13-17): auto-refresh con `ACCESS_TOKEN_LIFETIME=15s` temporal.
- **Fase D** (18-21): logout limpia cookie + store.
- **Fase E** (22-23): screenshots + cierre de Playwright.

**Salida esperada:** las 3 capturas (`plan-validacion-dashboard.png`, `plan-validacion-logout.png`, `login-page.png`) + consola sin warnings de CORS + auto-refresh confirmado en network log.

### Paso 12 — Validar compose de Dokploy
- `docker compose -f docker-compose.dokploy.yml config --quiet` (debe pasar sin errores)
- **No** se ejecuta `up` localmente porque depende de la red `dokploy-network` externa.

### Paso 13 — Crear `START.md` (guía de rename de la plantilla)
- Crear `START.md` en la raíz con la guía completa de find-and-replace
  + cambios manuales + checklist de verificación (ver sección 4.18 abajo
  y el archivo ya creado en la raíz del proyecto).
- **Verificar:** `head -30 START.md` debe mostrar la sección 0 (pre-requisitos).

### 4.18 `START.md` (CREAR — guía de rename)

`START.md` es la guía que seguirá cualquier developer que use esta
plantilla como base para un proyecto real. Su objetivo es que en
**5-10 minutos** pueda renombrar `plantilla-django-react` a su nombre
de proyecto.

**Secciones del archivo (6):**
1. **Pre-requisitos** — definir 3 variantes del nombre (kebab, snake, Title).
2. **Find-and-replace global** — un único comando `find ... -exec sed`
   que reemplaza `plantilla_django_react` → `<project_snake>` y
   `plantilla-django-react` → `<project_kebab>` en todos los archivos
   relevantes, excluyendo `.git/`, `node_modules/`, `.venv/`, `__pycache__/`,
   `dist/`, `planes/`, `.serena/`, `.opencode/`, `.claude/`.
3. **Cambios manuales post-find-and-replace** (11 sub-secciones):
   2.1 Mover `backend/plantilla_django_react/` → `backend/<project_snake>/`
   2.2 Verificar `pyproject.toml` (name + include)
   2.3 Verificar `celery_app.py`
   2.4 Volúmenes Docker (renombrar en los 3 compose files)
   2.5 Dominio `plantilla.yoyodr.dev` → tu dominio
   2.6 `frontend/package.json` (name)
   2.7 Regenerar `DJANGO_SECRET_KEY` + `ALLOWED_HOSTS`
   2.8 `POSTGRES_DB` + password
   2.9 Reescribir `README.md`
   2.10 `backend/.gitignore` (`<project_snake>/media/`)
   2.11 Regenerar `uv.lock` con `uv lock`
4. **Apps Django internas** — qué apps vienen con la plantilla y cómo añadir nuevas.
5. **Verificación post-rename** — 5 comandos para confirmar que todo funciona
   (grep residual, build, up, createsuperuser, login en navegador).
6. **Limpieza opcional** — borrar `START.md`, `planes/`, configs de agentes.
7. **Checklist final** — 12 checks verificables.

**Por qué se incluye en este plan:**
- La plantilla es genérica y **debe** indicar a futuros usuarios cómo
  personalizarla. Sin `START.md`, el primer día de cualquier proyecto
  basado en esta plantilla se pierde buscando dónde cambiar el nombre.
- Es 1 archivo, 0 dependencias, ~5KB. ROI altísimo.

---

## 7. Riesgos y mitigaciones

| Riesgo | Mitigación |
| --- | --- |
| Volumen `/app/.venv` se monta vacío al primer build → UV re-instala | Aceptable; ya está documentado así en yodumanager y el Dockerfile de plantilla ya usa `mount=type=cache` para `/root/.cache/uv` (línea 24-27). |
| `mkdir -p /app/logs` no se persiste entre reinicios | El contenedor se rebuildea, los logs se recrean. Para persistir en Dokploy, ya está el volumen `production_django_logs:/app/logs` (sección 4.7). |
| `Dockerfile` local de yodumanager usa `python:3.12-slim-bookworm`, plantilla usa `ghcr.io/astral-sh/uv:python3.14-bookworm-slim` | **No se toca** — el usuario pidió mantener UV. |
| `vite.config.js` actual no tiene `usePolling` → HMR no detecta cambios en contenedor | Se añade en sección 4.16.9. |
| Dokploy-network ya creada con otro nombre | El nombre `dokploy-network` es convención, se puede renombrar en sección 4.7 si el cluster usa otro. |
| Imagen base de Postgres 18 en plantilla vs 17 en yodumanager | **No se toca** — solo afecta a la versión mayor de Postgres, no a la infra de frontend. |
| Variables de entorno frontend en Dokploy: el ARG `VITE_API_URL` se inyecta en build, no en runtime | Si cambia el dominio post-build, hay que rebuildear. Documentar en README. |
| **`pgbouncer` con `pool_mode = transaction` no soporta `manage.py migrate`** (DDL se aborta a mitad en conexiones pooled) | Documentado en yodumanager:40-46. Se mitiga con la receta `just manage-direct-db` que sobreescribe `PGB_POSTGRES_HOST=postgres` para conectar directo. Aplicar mismo parche al `justfile` de plantilla (Paso 5b). |
| **`pgbouncer` con `auth_type = md5` requiere que el password de Postgres sea md5-hashed** (no SCRAM-SHA-256 que es el default en Postgres 14+) | El `userlist.txt` se genera con `"${POSTGRES_USER}" "${POSTGRES_PASSWORD}"` (texto plano). pgbouncer hashea internamente con md5 y lo compara con la respuesta del cliente. Funciona contra Postgres 18 con `password_encryption = md5` o con SCRAM si el cliente negocia. Si falla la conexión: añadir `password_encryption = md5` en `postgresql.conf` del Dockerfile de postgres (no se hace en el plan inicial; documentar como fix si aparece). |
| **`pgbouncer` en Dokploy necesita acceso al Postgres de Dokploy por la red `dokploy-network`** | El hostname se inyecta como `POSTGRES_HOST` en runtime. Si Dokploy usa otro mecanismo de conexión para Postgres (ej. `db:5432` interno), ajustar el `.envs/.production/.postgres`. |
| **Si se añaden 2+ replicas de `celeryworker` con `docker compose up --scale celeryworker=2` antes de que pgbouncer esté listo**, race condition | `depends_on: [pgbouncer]` solo espera a que el contenedor arranque, no a que esté listo. Los workers reintentan la conexión a Redis y pgbouncer reintenta a postgres → se autorecupera en ~10s. Aceptable. |
| **`celery_app.py` usa `Celery("plantilla_django_react")` (no `"backend"` como yodumanager)** | Solo afecta al `__name__` del worker, no a la config. Sin acción. Documentado en 2.3.1. |
| **CORS preflight falla al enviar cookies con `credentials: 'include'`** | `django-cors-headers` exige `CORS_ALLOW_CREDENTIALS = True` y NO permite `Access-Control-Allow-Origin: *` cuando hay credenciales. El plan configura `CORS_ALLOW_CREDENTIALS = True` en `local.py` y `DJANGO_CORS_ALLOWED_ORIGINS` con origen explícito. |
| **SimpleJWT `ACCESS_TOKEN_LIFETIME=5min` muy corto en dev** | Documentar en `.envs/.local/.django` como override opcional: `SIMPLE_JWT_ACCESS_LIFETIME_MINUTES=60`. Si se quiere configurable, hay que añadir el override en `base.py:ACCESS_TOKEN_LIFETIME = timedelta(minutes=env.int("SIMPLE_JWT_ACCESS_LIFETIME_MINUTES", default=5))`. Se documenta como follow-up, no se implementa en este plan. |
| **Race condition: 2+ requests 401 concurrentes disparan 2 refreshes** | El plan implementa el patrón `isRefreshing` + `failedQueue` (copiado de yodumanager). Solo la primera request ejecuta el refresh; las demás se encolan. Al terminar el refresh, todas se reintentan con el nuevo token. |
| **Refresh token rotation no implementada (`ROTATE_REFRESH_TOKENS=False`)** | Copiado de yodumanager. Si se quiere rotation, se cambia a `True` y se añade `rest_framework_simplejwt.token_blacklist` a `INSTALLED_APPS` + migración. Se documenta como follow-up. |
| **`fetch` no lanza error en 4xx/5xx** (a diferencia de axios) | El `apiClient.js` lo maneja: chequea `response.ok` y construye un `Error` con `.status` y `.data`. El consumidor ve errores normales. |
| **`fetch` no serializa FormData automáticamente** | El `apiClient.js` detecta `FormData` y NO le pone `Content-Type` (lo gestiona el navegador). Documentado en el JSDoc. |
| **AbortController/timeout no implementado** | El `apiClient.js` base no tiene timeout. Si se necesita, se puede añadir un `AbortController` opcional. Documentado como follow-up. |

---

## 8. Verificación final (checklist)

Antes de marcar como hecho:

**Infra:**
- [ ] `docker compose -f docker-compose.local.yml config --quiet` pasa
- [ ] `docker compose -f docker-compose.dokploy.yml config --quiet` pasa
- [ ] `docker compose -f docker-compose.production.yml config --quiet` pasa
- [ ] `docker compose -f docker-compose.local.yml up -d --build` arranca todos los servicios (postgres, pgbouncer, redis, django, celeryworker, celerybeat, flower, frontend)
- [ ] `just manage-direct-db migrate` corre sin errores (bypass pgbouncer)
- [ ] `just manage-direct-db createsuperuser` funciona
- [ ] `docker compose -f docker-compose.local.yml exec django python -c "import django; django.setup(); from django.db import connection; connection.ensure_connection()"` conecta vía pgbouncer
- [ ] `docker compose -f docker-compose.local.yml logs celeryworker | head -20` muestra "celery@<hostname> ready"
- [ ] `docker compose -f docker-compose.local.yml logs celerybeat | head -10` muestra "beat: Starting..."

**Backend (auth SimpleJWT):**
- [ ] `curl -X POST http://localhost:8000/api/auth/token -H "Content-Type: application/json" -d '{}'` → 400 con detalle de campos faltantes
- [ ] `curl -X POST http://localhost:8000/api/auth/token -H "Content-Type: application/json" -d '{"username":"admin@example.com","password":"<pwd>"}' -i` → 200 con `Set-Cookie: refresh_token=...; HttpOnly; SameSite=Lax` y body `{access, user}`
- [ ] `curl -X POST http://localhost:8000/api/auth/token/refresh -b "refresh_token=<value>"` → 200 con `{access}` y `Set-Cookie` actualizada
- [ ] `curl -X POST http://localhost:8000/api/auth/logout -b "refresh_token=<value>"` → 200 y `Set-Cookie: refresh_token=; expires=<pasado>`
- [ ] `curl http://localhost:8000/api/users/me/ -H "Authorization: Bearer <access>"` → 200 con datos del usuario
- [ ] `curl http://localhost:8000/api/users/me/` (sin token) → 401

**Frontend (login + CORS + auto-refresh):**
- [ ] `curl -I http://localhost:5173/` → 200 con `text/html`
- [ ] Abrir `http://localhost:5173/` → aparece formulario de Login
- [ ] Login con superusuario válido → pantalla cambia a "CORS OK ✅ — SimpleJWT funcionando"
- [ ] DevTools → Application → Cookies → ver `refresh_token` HttpOnly
- [ ] DevTools → Network → ver request `POST /api/auth/token` con `Access-Control-Allow-Origin: http://localhost:5173` y `Access-Control-Allow-Credentials: true`
- [ ] DevTools → Network → ver request `GET /api/users/me/` con `Authorization: Bearer <access>` y respuesta 200
- [ ] Esperar 5+ minutos (ACCESS_TOKEN_LIFETIME) o cambiar a 10s en dev y recargar → debe hacer un `POST /api/auth/token/refresh` transparente y luego `GET /api/users/me/` con éxito
- [ ] Click "Cerrar sesión" → vuelve al LoginPage y la cookie `refresh_token` se borra
- [ ] `docker compose -f docker-compose.local.yml logs django | head -20` no muestra errores

**Frontend (validación automatizada con Playwright MCP — Paso 11b):**
- [ ] `playwright_browser_navigate('http://localhost:5173/')` carga sin errores
- [ ] `playwright_browser_wait_for({text: 'Login'})` confirma render del form
- [ ] Login con credenciales válidas → `wait_for({text: 'CORS OK'})` confirma dashboard
- [ ] `playwright_browser_evaluate(() => document.cookie.includes('refresh_token'))` → **`false`** (cookie HttpOnly, aserción crítica)
- [ ] `playwright_browser_evaluate(() => localStorage.getItem('auth-settings'))` → contiene `state.access` y `state.user`
- [ ] Network log: `POST /api/auth/token` con `access-control-allow-origin: http://localhost:5173` + `access-control-allow-credentials: true`
- [ ] Network log: `GET /api/users/me/` con `authorization: Bearer <access>` → 200
- [ ] Network log: `POST /api/auth/token/refresh` ejecutado tras `ACCESS_TOKEN_LIFETIME=15s` (auto-refresh transparente)
- [ ] Click "Cerrar sesión" → vuelve a LoginPage + `POST /api/auth/logout` con 200 + `set-cookie: refresh_token=; expires=...`
- [ ] `playwright_browser_console_messages({level: 'warning'})` → sin warnings de CORS
- [ ] Screenshots guardados: `plan-validacion-dashboard.png` y `plan-validacion-logout.png`

**Limpieza:**
- [ ] `docker compose -f docker-compose.local.yml down` deja el sistema limpio
- [ ] `git status` muestra solo los archivos esperados en el plan
- [ ] `package.json` no contiene `"axios"` (verificar con `grep axios frontend/package.json`)

---

## 9. Próximos pasos fuera de este plan (no se ejecutan)

Cuando el usuario quiera pasar a auth real, se puede:
1. Reemplazar `obtain_auth_token` por `SimpleJWT` (`/api/token/` + `/api/token/refresh/`).
2. Añadir `react-router-dom` para tener rutas `/login`, `/dashboard`, etc.
3. Añadir Celery + pgBouncer si la app crece.
4. Crear `docker-compose.staging.yml` separado de `production.yml`.
5. Configurar GitHub Actions para `docker buildx` y push a registry.
6. Configurar backups automáticos de Postgres (los scripts de
   `maintenance/` ya están en plantilla, listos para usar).

**Una vez ejecutado y validado este plan, el siguiente paso lógico es
usar esta plantilla para un proyecto real.** La guía para hacerlo está
en `START.md` (creado en este plan) — ahí se documenta el
find-and-replace global, los cambios manuales y la verificación
post-rename.

---

## 10. Archivos relevantes (referencia rápida)

**Origen (yodumanager-v2):**
- `docker-compose.dokploy.yml:1-154` — fuente principal del compose Dokploy
- `docker-compose.local.yml:42-54, 107-122` — fuente de `pgbouncer` local y `frontend` local
- `docker-compose.local.yml:29-40` — fuente de `postgres` local
- `frontend/compose/local/Dockerfile:1-23` — fuente Dockerfile dev
- `frontend/compose/production/Dockerfile:1-55` — fuente Dockerfile prod
- `frontend/compose/production/nginx/nginx.conf:1-86` — fuente nginx config
- `frontend/start-frontend:1-14` — fuente del wait-for-django
- `frontend/.env:1-14` — fuente del env
- `frontend/src/api/axiosService.js:1-239` — **fuente del patrón auto-refresh + cola** (reescrito en fetch)
- `frontend/src/api/apiEndpoints.js:1-106` — fuente del patrón de constantes centralizadas (adaptado al mínimo)
- `frontend/src/store/authStore.js:1-45` — fuente del store Zustand con persist
- `backend/apps/custom_auth/api/viewsets/token.py:1-90` — fuente de los 3 views (login/refresh/logout)
- `backend/apps/custom_auth/api/serializers/token.py:1-20` — fuente del custom token serializer (adaptado, sin UserDetailSerializer)
- `backend/apps/custom_auth/api/router.py:1-21` — fuente del router de auth (sin register/forgot-password)
- `backend/config/settings/base.py` (bloque `SIMPLE_JWT` y `JWT_COOKIE_*`) — fuente de la config SimpleJWT
- `backend/requirements/base.txt` línea `djangorestframework-simplejwt==5.5.1` — fuente de la versión
- `backend/.envs/.local/.django:6-8, 11` — fuente de CORS / CSRF / LOG_FILE / JWT_REFRESH_TOKEN_DAYS
- `backend/.envs/.local/.postgres:11-15` — fuente del bloque `PGB_*`
- `backend/.envs/.local/.django:33` — fuente del `DATABASE_URL` apuntando a pgbouncer
- `backend/compose/local/django/Dockerfile:7` — fuente del `mkdir -p /app/logs`
- `backend/compose/production/django/Dockerfile:91-92` — fuente de `mkdir -p /app/logs` en producción
- `backend/compose/production/django/entrypoint:1-17` — fuente del entrypoint con pgbouncer
- `backend/compose/production/django/celery/{worker,beat,flower}/start` — fuente de los start scripts (con `mkdir -p /app/logs` ya incluido)
- `backend/compose/production/pgbouncer/Dockerfile:1-40` — fuente del Dockerfile pgbouncer
- `backend/compose/production/pgbouncer/entrypoint.sh:1-23` — fuente del entrypoint pgbouncer
- `backend/compose/production/pgbouncer/pgbouncer.ini.template:1-18` — fuente del config
- `backend/compose/production/pgbouncer/userlist.template.txt:1` — fuente del userlist
- `backend/justfile:40-50` — fuente de `manage-direct-db` y `pytest` con bypass pgbouncer

**Destino (plantilla-django-react):**
- `docker-compose.local.yml` — añadir `pgbouncer` + cambiar `depends_on` + añadir `frontend` (Paso 5)
- `docker-compose.production.yml` — añadir `pgbouncer` + cambiar `depends_on` + añadir `frontend` (Paso 5)
- `docker-compose.dokploy.yml` — crear (Paso 6)
- `backend/compose/local/django/Dockerfile` — añadir `logs` (Paso 4)
- `backend/compose/production/django/Dockerfile` — añadir `logs` (Paso 4)
- `backend/compose/production/django/entrypoint` — usar `PGB_POSTGRES_*` (Paso 4a)
- `backend/compose/production/django/celery/worker/start` — añadir `logs` (Paso 4b)
- `backend/compose/production/django/celery/beat/start` — añadir `logs` (Paso 4b)
- `backend/compose/production/django/celery/flower/start` — añadir `logs` (Paso 4b)
- `backend/compose/production/pgbouncer/Dockerfile` — crear (Paso 4c)
- `backend/compose/production/pgbouncer/entrypoint.sh` — crear (Paso 4c)
- `backend/compose/production/pgbouncer/pgbouncer.ini.template` — crear (Paso 4c)
- `backend/compose/production/pgbouncer/userlist.template.txt` — crear (Paso 4c)
- `backend/justfile` — añadir `manage-direct-db` y `pytest` (Paso 5b)
- `backend/.envs/.local/.django` — añadir CORS + DATABASE_URL→pgbouncer + JWT_* (Paso 1, 4.15.11)
- `backend/.envs/.local/.postgres` — añadir bloque `PGB_*` (Paso 4d)
- `backend/.envs/.production/.django` — añadir CORS + DATABASE_URL→pgbouncer + JWT_* (Paso 1, 4.15.11)
- `backend/.envs/.production/.postgres` — añadir bloque `PGB_*` (Paso 4e)
- `backend/pyproject.toml` — añadir simplejwt (Paso 4.15.1)
- `backend/config/settings/base.py` — añadir `rest_framework_simplejwt`, `SIMPLE_JWT`, `JWT_COOKIE_*`, JWTAuthentication (Paso 4.15.8)
- `backend/config/settings/local.py` — añadir `CORS_ALLOW_CREDENTIALS = True` (Paso 4.15.9)
- `backend/config/urls.py` — eliminar `obtain_auth_token`, añadir include de custom_auth (Paso 4.15.10)
- `backend/plantilla_django_react/custom_auth/apps.py` — crear (Paso 4.15.3)
- `backend/plantilla_django_react/custom_auth/api/router.py` — crear (Paso 4.15.6)
- `backend/plantilla_django_react/custom_auth/api/serializers/token.py` — crear (Paso 4.15.4)
- `backend/plantilla_django_react/custom_auth/api/viewsets/token.py` — crear (Paso 4.15.5)
- `frontend/compose/local/Dockerfile` — crear (Paso 2)
- `frontend/compose/production/Dockerfile` — crear (Paso 2)
- `frontend/compose/production/nginx/nginx.conf` — crear (Paso 2)
- `frontend/start-frontend` — crear (Paso 2)
- `frontend/.env` — crear (Paso 2)
- `frontend/.dockerignore` — crear (Paso 2)
- `frontend/vite.config.js` — añadir `host 0.0.0.0`, `usePolling`, alias `@` (Paso 4.16.9)
- `frontend/src/main.jsx` — añadir `initApiClient()` (Paso 4.16.6)
- `frontend/src/App.jsx` — reemplazar placeholder (Paso 4.16.8)
- `frontend/src/api/apiClient.js` — **crear (servicio base reusable)** (Paso 4.16.2)
- `frontend/src/api/apiEndpoints.js` — crear (Paso 4.16.3)
- `frontend/src/api/authService.js` — crear (Paso 4.16.5)
- `frontend/src/store/authStore.js` — crear (Paso 4.16.4)
- `frontend/src/pages/LoginPage.jsx` — crear (Paso 4.16.7)
- `frontend/package.json` — **quitar axios, añadir zustand** (Paso 4.16.1)
- `START.md` — **crear (guía de rename de la plantilla, 6 secciones, 0 dependencias)** (Paso 13 / sección 4.18)
- `planes/plan-ajuste-docker-despliegue-yodumanager.md` — este plan (referencia de cambios)
