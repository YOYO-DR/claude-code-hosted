"""Provisiona un dispositivo TOTP para un usuario e imprime la URL otpauth://
(para escanear en la app de autenticación). Idempotente por nombre de device."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django_otp.plugins.otp_totp.models import TOTPDevice


class Command(BaseCommand):
    help = "Crea/renueva un dispositivo TOTP confirmado para un usuario."

    def add_arguments(self, parser) -> None:
        parser.add_argument("username")
        parser.add_argument("--name", default="default")

    def handle(self, *args, **opts) -> None:
        User = get_user_model()
        try:
            user = User.objects.get(username=opts["username"])
        except User.DoesNotExist as exc:
            raise CommandError(f"Usuario '{opts['username']}' no existe") from exc

        TOTPDevice.objects.filter(user=user, name=opts["name"]).delete()
        device = TOTPDevice.objects.create(user=user, name=opts["name"], confirmed=True)
        self.stdout.write(self.style.SUCCESS("Dispositivo TOTP creado."))
        self.stdout.write("Escanéalo en tu app de autenticación (o pega la clave):")
        self.stdout.write(device.config_url)
