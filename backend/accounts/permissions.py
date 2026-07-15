from django.conf import settings
from rest_framework.permissions import BasePermission

from .models import AdminProfile


def get_role(user):
    if not user or not user.is_authenticated:
        return None
    try:
        return user.admin_profile.role
    except AdminProfile.DoesNotExist:
        return None


class IsAdminOrIT(BasePermission):
    """Only admin/it can add, edit, delete, or bulk-import users."""

    def has_permission(self, request, view):
        return get_role(request.user) in (AdminProfile.Role.ADMIN, AdminProfile.Role.IT)


class IsSecurityOrAbove(BasePermission):
    """Any recognized dashboard role - used for read-only views like logs and live monitoring."""

    def has_permission(self, request, view):
        return get_role(request.user) in (
            AdminProfile.Role.ADMIN,
            AdminProfile.Role.SECURITY,
            AdminProfile.Role.IT,
        )


class HasServiceToken(BasePermission):
    """Authenticates the entry-agent via a shared-secret header instead of a user JWT."""

    def has_permission(self, request, view):
        token = request.headers.get("X-Service-Token")
        return bool(token) and token == settings.ENTRY_AGENT_SERVICE_TOKEN
