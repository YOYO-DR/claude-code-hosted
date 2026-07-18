"""
Módulo de rotación automática de logs.

Este módulo contiene tareas de Celery para gestionar la rotación, compresión y limpieza
automática de archivos de log en el sistema. Implementa una estrategia de retención
basada en políticas de tiempo definidas.

Dependencies:
    - celery: Para tareas asíncronas
    - django: Framework web
    - pathlib: Manipulación de rutas
    - gzip: Compresión de archivos
"""

import gzip
import logging
import os
import shutil
import subprocess
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path

from celery import shared_task
from django.conf import settings

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    name="utils.rotate_logs",
    soft_time_limit=120,
    time_limit=180,
)
def rotate_logs_task(self):
    """
    Tarea Celery para realizar la rotación diaria de logs del sistema.

    Esta función implementa un sistema de rotación de logs que:
    1. Copia los logs actuales con sufijo de fecha del día anterior
    2. Trunca los archivos originales para comenzar frescos
    3. Limpia logs antiguos (comprime y elimina según políticas)
    4. Fuerza la reapertura de handlers de logging

    Args:
        self: Instancia de la tarea Celery (bind=True)

    Returns:
        dict: Resultado de la operación con estructura:
            - status (str): "SUCCESS" o "ERROR"
            - rotated_files (list): Lista de archivos rotados exitosamente
            - compressed_files (list): Lista de archivos comprimidos
            - deleted_files (list): Lista de archivos eliminados
            - timestamp (str): Timestamp ISO de la operación
            - error (str, opcional): Mensaje de error si status="ERROR"

    Raises:
        Exception: Captura todas las excepciones y las registra en el log

    Note:
        - Configurado con soft_time_limit=120s y time_limit=180s
        - Procesa el archivo configurado en DJANGO_NAME_LOG_FILE (default: django_dev.log)
        - Envía señales USR1 para reabrir logs en procesos activos (sin Docker)
        - Política de retención: comprimir >7 días, eliminar >30 días
    """
    try:
        log_dir = Path(settings.BASE_DIR) / "logs"
        rotated_files = []

        log_django_system = "django_dev.log"
        if os.getenv("DJANGO_NAME_LOG_FILE", None):
            log_django_system = f"{os.getenv('DJANGO_NAME_LOG_FILE')}.log"
        log_files = [
            log_django_system,
        ]
        yesterday = datetime.now(UTC) - timedelta(days=1)
        date_suffix = yesterday.strftime("%Y-%m-%d")

        for log_filename in log_files:
            log_path = log_dir / log_filename

            if not log_path.exists() or log_path.stat().st_size == 0:
                continue

            try:
                rotated_path = log_dir / f"{log_filename}.{date_suffix}"
                shutil.copy2(log_path, rotated_path)

                with log_path.open("w") as f:
                    f.truncate(0)

                rotated_files.append(log_filename)
                logger.info(
                    "Log rotado exitosamente: %s -> %s",
                    log_filename,
                    rotated_path.name,
                )

            except (OSError, PermissionError, FileNotFoundError):
                logger.exception("Error rotando archivo %s", log_filename)

        cleanup_result = cleanup_old_logs(log_dir, days_to_keep=30)

        if os.name == "posix" and os.getenv("USE_DOCKER") != "yes":
            try:
                subprocess.run(  # noqa: S603
                    ["/usr/bin/pkill", "-USR1", "-f", "manage.py"],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                subprocess.run(  # noqa: S603
                    ["/usr/bin/pkill", "-USR1", "-f", "celery"],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                logger.info("Señales USR1 enviadas exitosamente para reabrir logs")
            except subprocess.TimeoutExpired:
                logger.warning(
                    "Timeout enviando señales USR1 - continuando sin reabrir logs",
                )
            except (OSError, subprocess.SubprocessError, FileNotFoundError) as e:
                logger.warning("No se pudo enviar señal USR1 para reabrir logs: %s", e)
        else:
            logger.info(
                "Docker detectado o sistema no-POSIX - omitiendo señales USR1",
            )

        result = {
            "status": "SUCCESS",
            "rotated_files": rotated_files,
            "compressed_files": cleanup_result["compressed"],
            "deleted_files": cleanup_result["deleted"],
            "timestamp": datetime.now(UTC).isoformat(),
        }

        logger.info("Rotación de logs completada exitosamente: %s", result)

    except (OSError, PermissionError, ValueError, TypeError) as e:
        logger.exception("Error crítico en rotación de logs")
        return {
            "status": "error",
            "error": str(e),
            "timestamp": datetime.now(UTC).isoformat(),
        }
    else:
        return result


def cleanup_old_logs(log_dir: Path, days_to_keep: int = 30):
    """
    Gestiona archivos de log antiguos aplicando políticas de retención.

    Implementa una estrategia de retención escalonada:
    - Logs recientes (0-7 días): Se mantienen sin comprimir
    - Logs intermedios (7-30 días): Se comprimen con gzip para ahorrar espacio
    - Logs antiguos (+30 días): Se eliminan completamente

    Args:
        log_dir (Path): Directorio que contiene los archivos de log
        days_to_keep (int, opcional): Días antes de eliminar definitivamente.
                                     Por defecto 30 días.

    Returns:
        dict: Estadísticas de la operación:
            - compressed (list): Lista de archivos comprimidos exitosamente
            - deleted (list): Lista de archivos eliminados exitosamente

    Note:
        - Solo procesa archivos con patrón *.log.YYYY-MM-DD
        - Los archivos .gz ya comprimidos no se vuelven a comprimir
        - Usa el timestamp de modificación (mtime) para determinar la edad
    """
    now = datetime.now(UTC)
    compress_cutoff = now - timedelta(days=7)
    delete_cutoff = now - timedelta(days=days_to_keep)

    compressed_files = []
    deleted_files = []

    for log_file in log_dir.glob("*.log.*"):
        try:
            file_mtime = datetime.fromtimestamp(log_file.stat().st_mtime, UTC)

            if file_mtime < delete_cutoff:
                log_file.unlink()
                deleted_files.append(log_file.name)
                logger.info(
                    "Eliminado log antiguo: %s (edad: %s días)",
                    log_file.name,
                    (now - file_mtime).days,
                )

            elif file_mtime < compress_cutoff and not log_file.name.endswith(".gz"):
                compressed_path = Path(f"{log_file}.gz")
                with (
                    log_file.open("rb") as f_in,
                    gzip.open(compressed_path, "wb") as f_out,
                ):
                    shutil.copyfileobj(f_in, f_out)
                log_file.unlink()
                compressed_files.append(log_file.name)
                logger.info(
                    "Comprimido log: %s -> %s (edad: %s días)",
                    log_file.name,
                    compressed_path.name,
                    (now - file_mtime).days,
                )

        except (OSError, PermissionError, gzip.BadGzipFile) as e:
            logger.warning("No se pudo procesar archivo %s: %s", log_file, e)

    return {
        "compressed": compressed_files,
        "deleted": deleted_files,
    }
