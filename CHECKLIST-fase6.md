# CHECKLIST-fase6.md — confirmación manual de Yoiner

Todo lo automatizable + los e2e/caos en vivo están verdes (95 tests; ver
PROGRESS.md → Gate 6). Faltan cosas que dependen de ti.

## Ya verificado en vivo (por mí)
- Backup cifrado + **restore real** en DB limpia (datos y tokens cifrados intactos).
- Alerta de **disco 95%** enviada al topic sistema de Telegram.
- Caos: Redis/PG caídos → recuperan sin pérdida; **reboot frío** → infra+panel
  vuelven solos y las 3 sesiones quedan `crashed` (estado honesto).

## Pendiente de ti
- [ ] Confirma que viste las alertas de disco en el topic **sistema** del grupo.
- [ ] **Branch protection** en los repos donde trabajen agentes (candado real de
      "no merge").
- [ ] Guarda una copia de `/etc/panel/backup.pass` fuera del VPS (sin ella los
      backups no se descifran).

## S3/MinIO — verificado en vivo

`scripts/s3_backup.py check` valida el bucket (`claude-code-hosted` en
`https://s3-minio-zybx86-…sslip.io`); el backup diario sube + retención
remota (14). Round-trip: descargado de S3 → descifrado con la passphrase →
`pg_dump` íntegro (header `PGDMP`, 389 KB).

## Notas
- Timers: `backup.timer` (03:30 diario), `monitor.timer` (cada 2 min).
- Runbook completo de operación en `REPORT.md` (arrancar/parar, restaurar,
  rotar tokens, alertas, caos).
