"""API REST v1 para el SPA React (FASE C.3).

Todos los endpoints:
- Requieren `is_authenticated` Y `is_verified` (TOTP).
- Devuelven JSON (no renderizan HTML).
- Aceptan CORS same-origin (mismo dominio que los templates legacy).
- Usan la sesión Django (cookie + CSRF) — NO JWT.

Endpoints expuestos:
    GET  /api/v1/me/                       CurrentUser
    POST /api/v1/login/                    {username, password, otp_token}
    POST /api/v1/logout/
    GET  /api/v1/sessions/                 [Session]
    GET  /api/v1/sessions/<uuid>/          SessionDetail
    POST /api/v1/sessions/<uuid>/message/  {text}
    POST /api/v1/sessions/<uuid>/stop/
    GET  /api/v1/sessions/<uuid>/events/?since=<seq>  [UIEvent + seq]
    GET  /api/v1/projects/                 [Project]
    GET  /api/v1/projects/<slug>/          ProjectDetail
    GET  /api/v1/projects/<slug>/tree?path=
    GET  /api/v1/projects/<slug>/file?path=
    GET  /api/v1/projects/<slug>/diff?path=
    GET  /api/v1/mcps/                     [McpServer]
    GET  /api/v1/github/                   {has_token, result?}
    POST /api/v1/github/                   {token?}
    GET  /api/v1/permissions/              [PermissionRequest]
    POST /api/v1/permissions/<uuid>/resolve/  {answer}
"""