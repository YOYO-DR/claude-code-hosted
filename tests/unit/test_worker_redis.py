"""Regresión del bug 'Timeout reading from 127.0.0.1:6379' (§bug fase-7):

redis-py async trae DEFAULT_SOCKET_TIMEOUT=5s. El worker hace
`brpop([key], timeout=5)` y `read_response()` cae al socket_timeout cuando el
servidor responde nil a los 5s. Resultado: el worker entra en un bucle de
excepciones y nunca procesa mensajes.

El fix: instanciar el redis del Worker con socket_timeout=None (y lo mismo en
el consumer de Channels). Este test verifica el cableado para que no se
regrese."""
from __future__ import annotations

from unittest.mock import patch


def test_worker_redis_has_no_socket_timeout():
    """Worker.__init__ debe pasar socket_timeout=None al cliente redis async."""
    from workers import session_worker

    with patch("workers.session_worker.aioredis.from_url") as mocked:
        w = session_worker.Worker.__new__(session_worker.Worker)
        w.__init__("test-sid")
        kwargs = mocked.call_args.kwargs
        assert kwargs.get("socket_timeout") is None, (
            f"Worker redis client debe usar socket_timeout=None para que "
            f"brpop(timeout=5) no choque con el default de 5s; "
            f"got {kwargs.get('socket_timeout')!r}"
        )


def test_consumer_make_redis_has_no_socket_timeout():
    """SessionConsumer también: su pubsub.listen() cae al socket_timeout igual."""
    from panel.core import consumers

    with patch("panel.core.consumers.aioredis.from_url") as mocked:
        consumers.make_redis()
        kwargs = mocked.call_args.kwargs
        assert kwargs.get("socket_timeout") is None, (
            f"consumer redis client debe usar socket_timeout=None; "
            f"got {kwargs.get('socket_timeout')!r}"
        )


def test_channels_redis_layer_has_no_socket_timeout():
    """CHANNEL_LAYERS (channels_redis) también cae al socket_timeout en su
    pubsub.listen() — y este NO se puede monkey-patch en el consumer porque
    el pool se crea en settings. Hay que cablearlo en CHANNEL_LAYERS CONFIG."""
    # settings_test sobreescribe a InMemoryChannelLayer; leer el settings real.
    import importlib
    import sys
    # Quitar el settings_test cargado para que se importe el real.
    for mod in [m for m in sys.modules if m.startswith("panel")]:
        del sys.modules[mod]
    import panel.settings as prod_settings  # noqa: PLC0415
    try:
        hosts = prod_settings.CHANNEL_LAYERS["default"]["CONFIG"]["hosts"]
        assert hosts, "CHANNEL_LAYERS no configurado en panel/settings.py"
        for host in hosts:
            assert host.get("socket_timeout") is None, (
                f"Cada host de CHANNEL_LAYERS debe llevar socket_timeout=None; "
                f"got {host.get('socket_timeout')!r}"
            )
    finally:
        # Restaurar el settings de test para no contaminar otros tests.
        for mod in [m for m in sys.modules if m.startswith("panel")]:
            del sys.modules[mod]
        importlib.invalidate_caches()