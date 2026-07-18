"""Context processors: badge global de aprobaciones pendientes (§ Fase 3.3)."""

from __future__ import annotations

from panel.core.models import PermissionRequest


def pending_permissions(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated or not user.is_verified():
        return {"pending_count": 0}
    return {
        "pending_count": PermissionRequest.objects.filter(
            status=PermissionRequest.Status.PENDING
        ).count()
    }
