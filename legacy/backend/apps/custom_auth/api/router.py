from django.conf import settings
from django.urls import path
from rest_framework.routers import DefaultRouter, SimpleRouter

from .viewsets.token import CustomTokenObtainPairView, CookieTokenRefreshView, LogoutView

router = DefaultRouter() if settings.DEBUG else SimpleRouter()

urlpatterns = [
    *router.urls,
    path("token", CustomTokenObtainPairView.as_view(), name="obtain_pair_token"),
    path("token/refresh", CookieTokenRefreshView.as_view(), name="refresh_token"),
    path("logout", LogoutView.as_view(), name="logout"),
]
