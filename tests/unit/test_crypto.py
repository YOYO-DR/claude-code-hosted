"""Cifrado de secretos con MultiFernet (rotación sin migración)."""

import pytest
from cryptography.fernet import Fernet, InvalidToken

from panel.core import crypto


def test_encrypt_decrypt_roundtrip():
    token = crypto.encrypt("sk-ant-secreto")
    assert isinstance(token, bytes)
    assert b"sk-ant-secreto" not in token  # no en claro
    assert crypto.decrypt(token) == "sk-ant-secreto"


def test_multifernet_rotation_decrypts_old(settings):
    old_key = Fernet.generate_key().decode()
    new_key = Fernet.generate_key().decode()

    settings.SECRET_ENC_KEYS = [old_key]
    old_token = crypto.encrypt("valor-viejo")

    # Rotación: la nueva clave va primera (cifra), la vieja sigue descifrando.
    settings.SECRET_ENC_KEYS = [new_key, old_key]
    assert crypto.decrypt(old_token) == "valor-viejo"
    new_token = crypto.encrypt("valor-nuevo")

    # Quitar la vieja: el token viejo ya no se descifra, el nuevo sí.
    settings.SECRET_ENC_KEYS = [new_key]
    assert crypto.decrypt(new_token) == "valor-nuevo"
    with pytest.raises(InvalidToken):
        crypto.decrypt(old_token)


def test_missing_keys_raises(settings):
    settings.SECRET_ENC_KEYS = []
    with pytest.raises(RuntimeError):
        crypto.encrypt("x")
