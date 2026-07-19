# scripts/chaos — pruebas de caos (§6.4)

Scripts para estresar la plataforma y verificar recuperación honesta. Correr
como root en el VPS. Cada uno limpia lo que ensucia.

| Script | Qué prueba |
|--------|-----------|
| `redis_outage.sh` | `docker stop` de Redis N s bajo carga → el worker no muere, reintenta, y al volver Redis procesa; cero eventos perdidos en PG. |
| `pg_outage.sh` | `docker stop` de Postgres N s → el worker falla limpio (no corrompe) y al volver opera. |
| `disk_fill.sh` | `fallocate` para llevar `/` >90% → el monitor alerta; luego libera. |
| `cold_reboot.sh` | reboot del VPS con sesiones vivas → al volver, infra+panel arriba y las sesiones sin heartbeat quedan `crashed` (estado honesto), no "running" fantasma. |

El **desfase de reloj ±5 min** no se prueba tocando el reloj del VPS (rompería
TLS): los timeouts de permisos usan `time.monotonic()` (worker `_wait_answer`),
inmune al reloj de pared. Verificado en `tests/unit/test_permissions.py`
(`test_wait_answer_monotonic_ignores_wall_clock`).
