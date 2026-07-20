# Panel SPA — FASE C

SPA React 19 + Vite + TypeScript + TanStack Router/Query para el panel
web de sesiones de Claude Code. Reemplaza progresivamente los templates
Django.

## Stack

- **React 19** + **TypeScript 5.7** estricto
- **Vite 6** — dev server con HMR + proxy a Django (`:8000`) para `/api` y `/ws`
- **TanStack Router** (manual, no file-based) + **TanStack Query**
- **Auth**: cookie de sesión Django + CSRF (no JWT)

## Scripts

```bash
pnpm install        # instalar deps
pnpm dev           # dev server en :5173, proxy a :8000
pnpm build         # tsc --noEmit + vite build → dist/
pnpm typecheck     # solo typecheck
pnpm preview       # servir dist/ localmente
```

## Estructura

```
src/
├── main.tsx               # entry: monta Router + QueryClient
├── router.tsx             # rutas manuales (createRoute)
├── pages/                 # páginas de la SPA
│   ├── Login.tsx
│   ├── Sessions.tsx
│   ├── SessionDetail.tsx  # chat OpenHands + panel lateral
│   ├── Projects.tsx
│   ├── Mcps.tsx
│   ├── Github.tsx
│   └── Permissions.tsx
├── components/            # componentes reutilizables (vacío por ahora)
├── lib/
│   ├── api.ts             # fetch con CSRF + credentials
│   ├── ws.ts              # cliente WS de eventos con reconexión
│   └── me.ts              # /api/v1/me/ (CurrentUser)
└── types/
    └── uievents.ts        # UIEvent v1 (discriminated union, FASE B)
```

## Cómo se integra con Django

- `pnpm build` produce `dist/` con `index.html` + `assets/`.
- Whitenoise sirve `dist/` bajo el mismo dominio (`/`).
- Las rutas Django legacy siguen en `/admin/`, `/login/` (HTML), etc.
- El consumidor de eventos WS ya existe (`/ws/session/<sid>/`).

## Estado actual (FASE C.1 cerrado)

- [x] Andamiaje Vite + React + TS estricto.
- [x] Login + TOTP vía `/api/v1/login/` (FASE C.3 pendiente en backend).
- [x] Lista de sesiones vía `/api/v1/sessions/`.
- [x] Vista Sesión estilo OpenHands con discriminated union UIEvent v1.
- [x] Cliente WS con reconexión + `last_seq` + `SeqDedup` cliente.
- [ ] Browser de archivos + diff (FASE C.5).
- [ ] Watcher de rama git (FASE C.6).
- [ ] CRUD de Proyectos / MCPs / GitHub / Aprobaciones (FASE C.7).

## Convenciones

- **No JWT**. Auth por cookie de sesión Django (ya validada en producción).
- **CSRF**: el cliente lee `csrftoken` de `document.cookie` y lo manda
  como `X-CSRFToken` en métodos no-seguros.
- **TypeScript estricto**: nada de `any` salvo en puntos de unión
  documentados. Errores de tipo = bloquea build.
- **Discriminated union por `kind`**: cualquier UIEvent se ramifica con
  `switch (ui.kind)`. Mantener sincronizado con `panel/core/events/normalize.py`.
- **fetch siempre con `credentials: "include"`**: el SPA no maneja tokens,
  hereda la sesión del navegador.