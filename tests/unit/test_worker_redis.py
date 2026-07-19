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
        mocked.return_value.connection_pool.max_connections = 10
        w = session_worker.Worker.__new__(session_worker.Worker)
        w.__init__("test-sid")
        kwargs = mocked.call_args.kwargs
        assert kwargs.get("socket_timeout") is None, (
            f"Worker redis client debe usar socket_timeout=None para que "
            f"brpop(timeout=5) no choque con el default de 5s; "
            f"got {kwargs.get('socket_timeout')!r}"
        )
        # limpieza
        w.redis.aclose  # noop si no se implementó; el mock no se conecta


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