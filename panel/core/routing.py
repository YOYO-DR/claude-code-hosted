from django.urls import re_path

from panel.core.consumers import SessionConsumer

websocket_urlpatterns = [
    re_path(r"^ws/session/(?P<sid>[0-9a-f-]{36})$", SessionConsumer.as_asgi()),
]
