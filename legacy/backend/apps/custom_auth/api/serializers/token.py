from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Añade los datos del usuario a la respuesta del login."""

    def validate(self, attrs):
        data = super().validate(attrs)

        user = self.user
        data["user"] = {
            "id": user.id,
            "email": user.email,
            "username": getattr(user, "username", None),
            "is_staff": user.is_staff,
            "is_superuser": user.is_superuser,
        }
        return data
