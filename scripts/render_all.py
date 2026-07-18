#!/usr/bin/env python
"""Re-materializa todos los proyectos activos (§4.3). Correr tras editar
config a mano en DB, o en despliegue. Idempotente.

    python scripts/render_all.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "panel.settings")

import django  # noqa: E402

django.setup()

from panel.core import renderer  # noqa: E402


def main() -> None:
    renderer.render_all()
    print("render_all: OK")


if __name__ == "__main__":
    main()
