from django.conf import settings
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

from ..serializers.token import CustomTokenObtainPairSerializer


class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)

        if response.status_code == status.HTTP_200_OK:
            refresh_token = response.data.get("refresh")

            if refresh_token:
                response.set_cookie(
                    key=settings.JWT_COOKIE_NAME,
                    value=refresh_token,
                    httponly=settings.JWT_COOKIE_HTTP_ONLY,
                    secure=settings.JWT_COOKIE_SECURE,
                    samesite=settings.JWT_COOKIE_SAMESITE,
                    max_age=int(settings.SIMPLE_JWT['REFRESH_TOKEN_LIFETIME'].total_seconds()),
                )
                if "refresh" in response.data:
                    del response.data["refresh"]
        return response


class CookieTokenRefreshView(TokenRefreshView):
    def post(self, request, *args, **kwargs):
        refresh_token = request.COOKIES.get(settings.JWT_COOKIE_NAME)

        data = request.data
        if hasattr(data, 'dict'):
            data = data.dict()
        elif isinstance(data, dict):
            data = data.copy()
        else:
            data = {}

        if refresh_token:
            data['refresh'] = refresh_token

        serializer = self.get_serializer(data=data)

        try:
            serializer.is_valid(raise_exception=True)
        except (InvalidToken, TokenError) as e:
            response = Response({"detail": str(e)}, status=status.HTTP_401_UNAUTHORIZED)
            if refresh_token:
                response.delete_cookie(settings.JWT_COOKIE_NAME)
            return response

        token_data = serializer.validated_data
        response = Response(token_data, status=status.HTTP_200_OK)

        if response.status_code == status.HTTP_200_OK:
            if "refresh" in response.data:
                response.set_cookie(
                    key=settings.JWT_COOKIE_NAME,
                    value=response.data["refresh"],
                    httponly=settings.JWT_COOKIE_HTTP_ONLY,
                    secure=settings.JWT_COOKIE_SECURE,
                    samesite=settings.JWT_COOKIE_SAMESITE,
                    max_age=int(settings.SIMPLE_JWT['REFRESH_TOKEN_LIFETIME'].total_seconds()),
                )
                del response.data["refresh"]
        return response


class LogoutView(APIView):
    permission_classes = []

    def post(self, request, *args, **kwargs):
        response = Response({"detail": "Successfully logged out."}, status=status.HTTP_200_OK)
        response.delete_cookie(settings.JWT_COOKIE_NAME)
        return response
