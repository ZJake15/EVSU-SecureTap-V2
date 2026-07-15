from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .models import AdminProfile


class RoleTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Embeds the dashboard role/full name in the JWT so the frontend can route by role
    without a separate profile lookup."""

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        try:
            role = user.admin_profile.role
        except AdminProfile.DoesNotExist:
            role = None
        token["role"] = role
        token["username"] = user.username
        token["full_name"] = user.get_full_name() or user.username
        return token

    def validate(self, attrs):
        data = super().validate(attrs)
        if not hasattr(self.user, "admin_profile"):
            raise serializers.ValidationError("This account has no dashboard role assigned.")
        return data
