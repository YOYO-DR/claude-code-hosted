#!/usr/bin/env python
"""Sube un backup a un bucket S3-compatible (MinIO por ahora, S3 real después) y
aplica retención remota. Config por entorno (panel.env):

    PANEL_BACKUP_S3_ENDPOINT     ej. https://minio.midominio.com  (vacío = AWS S3)
    PANEL_BACKUP_S3_BUCKET       nombre del bucket (requerido para subir)
    PANEL_BACKUP_S3_ACCESS_KEY
    PANEL_BACKUP_S3_SECRET_KEY
    PANEL_BACKUP_S3_REGION       ej. us-east-1 (default)
    PANEL_BACKUP_S3_PREFIX       prefijo/carpeta (default: panel/)
    PANEL_BACKUP_S3_RETENTION    nº de backups a conservar (default 14)

Uso:  s3_backup.py upload <archivo>     |     s3_backup.py check
Sin bucket configurado: no hace nada (exit 0), para que el backup local funcione
igual sin S3.
"""

from __future__ import annotations

import os
import sys


def _cfg() -> dict:
    return {
        "endpoint": os.environ.get("PANEL_BACKUP_S3_ENDPOINT", "").strip() or None,
        "bucket": os.environ.get("PANEL_BACKUP_S3_BUCKET", "").strip(),
        "access": os.environ.get("PANEL_BACKUP_S3_ACCESS_KEY", "").strip(),
        "secret": os.environ.get("PANEL_BACKUP_S3_SECRET_KEY", "").strip(),
        "region": os.environ.get("PANEL_BACKUP_S3_REGION", "us-east-1").strip(),
        "prefix": os.environ.get("PANEL_BACKUP_S3_PREFIX", "panel/").strip().lstrip("/"),
        "retention": int(os.environ.get("PANEL_BACKUP_S3_RETENTION", "14")),
    }


def _client(cfg: dict):
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=cfg["endpoint"],
        aws_access_key_id=cfg["access"],
        aws_secret_access_key=cfg["secret"],
        region_name=cfg["region"],
    )


def upload(path: str) -> int:
    cfg = _cfg()
    if not cfg["bucket"]:
        print("S3 no configurado (sin bucket); se omite subida.")
        return 0
    s3 = _client(cfg)
    key = cfg["prefix"] + os.path.basename(path)
    s3.upload_file(path, cfg["bucket"], key)
    print(f"subido s3://{cfg['bucket']}/{key}")
    _prune(s3, cfg)
    return 0


def _prune(s3, cfg: dict) -> None:
    resp = s3.list_objects_v2(Bucket=cfg["bucket"], Prefix=cfg["prefix"])
    objs = sorted(resp.get("Contents", []), key=lambda o: o["LastModified"], reverse=True)
    for obj in objs[cfg["retention"]:]:
        s3.delete_object(Bucket=cfg["bucket"], Key=obj["Key"])
        print(f"retención: borrado s3://{cfg['bucket']}/{obj['Key']}")


def check() -> int:
    """Valida credenciales/bucket (para la UI/CLI). Imprime OK o el error."""
    cfg = _cfg()
    if not cfg["bucket"]:
        print("S3 no configurado")
        return 0
    try:
        s3 = _client(cfg)
        s3.head_bucket(Bucket=cfg["bucket"])
        print(f"OK: bucket '{cfg['bucket']}' accesible en {cfg['endpoint'] or 'AWS S3'}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR S3: {exc}")
        return 1


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "upload" and len(sys.argv) == 3:
        return upload(sys.argv[2])
    if len(sys.argv) == 2 and sys.argv[1] == "check":
        return check()
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
