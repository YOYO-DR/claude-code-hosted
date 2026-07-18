"""Hooks de reescritura de input de herramientas (§4.2 paso 4). Un hook recibe
`(tool_name, input_dict)` y devuelve un dict modificado, o None si no aplica.
El callback los aplica en orden ANTES de pedir aprobación, así el operador ve y
aprueba exactamente lo que se ejecutará.

Fase 3: lista base vacía + un hook dummy opcional (env, para el gate). El hook
real de puertos llega en Fase 4."""

from __future__ import annotations

import os
from collections.abc import Callable

RewriteHook = Callable[[str, dict], "dict | None"]

# Hooks permanentes (Fase 4 añadirá el de puertos aquí).
REWRITE_HOOKS: list[RewriteHook] = []


def _dummy_rename_hook(tool_name: str, input_data: dict) -> dict | None:
    """Hook dummy del gate 3: si se escribe un archivo llamado ORIGINAL.txt,
    reescribe el destino a REWRITTEN.txt. Solo activo con PANEL_REWRITE_DUMMY=1."""
    if tool_name != "Write":
        return None
    path = input_data.get("file_path", "")
    if path.endswith("ORIGINAL.txt"):
        return {**input_data, "file_path": path.replace("ORIGINAL.txt", "REWRITTEN.txt")}
    return None


def get_hooks() -> list[RewriteHook]:
    hooks = list(REWRITE_HOOKS)
    if os.environ.get("PANEL_REWRITE_DUMMY") == "1":
        hooks.append(_dummy_rename_hook)
    return hooks


def apply_rewrites(
    tool_name: str, input_data: dict, hooks: list[RewriteHook]
) -> tuple[dict, bool]:
    """Aplica los hooks en orden. Devuelve (input_efectivo, cambió)."""
    current = input_data
    changed = False
    for hook in hooks:
        out = hook(tool_name, current)
        if out is not None and out != current:
            current = out
            changed = True
    return current, changed
