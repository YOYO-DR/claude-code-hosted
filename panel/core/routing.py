from django.urls import re_path

from panel.core.consumers import SessionConsumer

websocket_urlpatterns = [
    # Acepta trailing "/" y "?last_seq=N" (query string ignorada por el
    # regex; el consumer lee el query desde el scope).
    re_path(r"^ws/session/(?P<sid>[0-9a-f-]{36})/?$", SessionConsumer.as_asgi()),
]
