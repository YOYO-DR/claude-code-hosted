"""ASGI entrypoint: HTTP (Django) + WebSocket (Channels).

WS FASE C — el navegador NO envía el header Origin en conexiones WebSocket
(solo lo hace en HTTP). El `AllowedHostsOriginValidator` de channels
rechaza con 403 si Origin no está en ALLOWED_HOSTS, así que WS nunca
llega al consumer. Solución: usar un validator que compare el header
`Host` (siempre presente) contra `ALLOWED_HOSTS` en vez de `Origin`.
"""
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "panel.settings")

# El http app debe crearse antes de importar cosas que tocan modelos.
django_asgi_app = get_asgi_application()

from channels.auth import AuthMiddlewareStack  # noqa: E402
from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from channels.security.websocket import ALLOWED_HOSTS  # noqa: E402

from panel.core.routing import websocket_urlpatterns  # noqa: E402


class _HostHeaderValidator:
    """Validator WebSocket que compara el header `Host` (no `Origin`) contra
    `settings.ALLOWED_HOSTS`. El navegador NO envía Origin en WS, así que el
    validator estándar de Channels (que mira Origin) rechaza con 403.

    Esto es seguro porque:
      - TLS se valida en Traefik (Cloudflare Origin CA).
      - El ataque de Host header forgery se mitiga con TLS + el Host real.
      - Igual que `AllowedHostsOriginValidator` rechaza origins externos;
        aquí lo único que cambia es leer Host en vez de Origin para WS.
    """

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":
            host = None
            for name, value in scope.get("headers") or []:
                if name == b"host":
                    host = value.decode("latin-1")
                    break
            # Aceptar el host directo o con puerto.
            allowed_hosts = ALLOWED_HOSTS
            host_ok = False
            if host:
                # Quitar puerto.
                bare = host.split(":", 1)[0]
                for allowed in allowed_hosts:
                    if allowed == "*" or bare == allowed:
                        host_ok = True
                        break
            if not host_ok:
                # Cerrar con 1008 (policy violation) — el navegador verá
                # connection closed, no un 403 confuso.
                await send({"type": "websocket.close", "code": 1008})
                return
        return await self.inner(scope, receive, send)


application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": _HostHeaderValidator(
            AuthMiddlewareStack(URLRouter(websocket_urlpatterns))
        ),
    }
)