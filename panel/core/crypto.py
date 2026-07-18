"""Cifrado de secretos en DB con MultiFernet (rotación sin migración: la 1ª
clave cifra, todas descifran). Ver PLAN.md §6 (Fernet desde el día uno)."""

from __future__ import annotations

from cryptography.fernet import Fernet, MultiFernet
from django.conf import settings


def _cipher() -> MultiFernet:
    keys = settings.SECRET_ENC_KEYS
    if not keys:
        raise RuntimeError("PANEL_SECRET_ENC_KEYS no configurado; no se puede cifrar/descifrar.")
    return MultiFernet([Fernet(k.encode() if isinstance(k, str) else k) for k in keys])


def encrypt(plaintext: str) -> bytes:
    return _cipher().encrypt(plaintext.encode())


def decrypt(token: bytes | memoryview) -> str:
    return _cipher().decrypt(bytes(token)).decode()
