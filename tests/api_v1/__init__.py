"""API v1: tests de endpoints JSON (FASE C.3 + C.5).

Cubre auth (login/me/logout), sesiones (list/detail/message/stop/events),
projects (tree/file/diff con path traversal tests), mcps, github,
permissions.

Los tests NO usan OTP — monkey-patchean `request.user.is_verified` o
trabajan con usuarios pre-verificados a través de helpers.
"""