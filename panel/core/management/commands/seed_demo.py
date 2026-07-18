"""Siembra el proyecto hardcoded de Fase 1: un ModelProfile Anthropic (default),
una PermissionPolicy en modo auto, y un Project 'demo' en /srv/projects/demo.
Idempotente."""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from panel.core.models import ModelProfile, PermissionPolicy, Project


class Command(BaseCommand):
    help = "Crea el proyecto demo de Fase 1 (idempotente)."

    def handle(self, *args, **opts) -> None:
        profile, _ = ModelProfile.objects.get_or_create(
            name="anthropic-default",
            defaults={"provider": ModelProfile.Provider.ANTHROPIC, "model": ""},
        )
        policy, _ = PermissionPolicy.objects.get_or_create(
            name="auto", defaults={"mode": PermissionPolicy.Mode.AUTO}
        )
        path = str(Path(settings.PROJECTS_ROOT) / "demo")
        project, created = Project.objects.get_or_create(
            slug="demo",
            defaults={
                "name": "Demo",
                "path": path,
                "model_profile": profile,
                "permission_policy": policy,
            },
        )
        verb = "creado" if created else "ya existía"
        self.stdout.write(self.style.SUCCESS(f"Proyecto demo {verb}: {project.path}"))
