"""Context processors: badge global de aprobaciones pendientes (§ Fase 3.3)."""
from __future__ import annotations

from panel.core.services import permissions as perm_svc


def pending_permissions(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated or not user.is_verified():
        return {"pending_count": 0}
    # D11: misma query que la vista `/permisos/` — sin divergencia entre badge
    # y lista (no más "reaparece"). Filtra por sesión viva + expires_at.
    return {"pending_count": perm_svc.live_pending_qs().count()}