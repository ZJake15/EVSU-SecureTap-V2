from rest_framework import status, viewsets
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.response import Response
from rest_framework_simplejwt.views import TokenObtainPairView

from audit.utils import log_action

from . import lockout
from .models import AdminProfile
from .permissions import IsAdmin
from .serializers import DashboardAccountSerializer, RoleTokenObtainPairSerializer


class LoginView(TokenObtainPairView):
    """Dashboard sign-in. With "Failed login lockout" switched on (Settings
    page), too many wrong passwords in a row lock the account for a while -
    checked here on the server, so it holds no matter what calls the API."""

    serializer_class = RoleTokenObtainPairSerializer

    def post(self, request, *args, **kwargs):
        username = request.data.get("username") if hasattr(request.data, "get") else None
        profile = None
        if isinstance(username, str) and username:
            profile = AdminProfile.objects.select_related("user").filter(user__username=username).first()

        message = lockout.locked_message(profile)
        if message:
            return Response({"detail": message}, status=status.HTTP_403_FORBIDDEN)

        try:
            response = super().post(request, *args, **kwargs)
        except AuthenticationFailed:
            lockout.record_failure(profile)
            raise

        lockout.clear_failures(profile)
        return response


class DashboardAccountViewSet(viewsets.ModelViewSet):
    """Account Management - Admin-only (see the access matrix: neither SASO
    nor Security Officer can create/edit/deactivate other dashboard logins).
    Every create/update/deactivate is audit-logged, since "who has access to
    this system and when it changed" is exactly the kind of thing an Admin
    needs to be able to answer later."""

    queryset = AdminProfile.objects.select_related("user").all().order_by("user__username")
    serializer_class = DashboardAccountSerializer
    permission_classes = [IsAdmin]

    def perform_create(self, serializer):
        instance = serializer.save()
        log_action(
            self.request.user, "account_created",
            target_description=f"Account {instance.user.username} ({instance.role})",
        )

    def perform_update(self, serializer):
        instance = serializer.save()
        log_action(
            self.request.user, "account_updated",
            target_description=f"Account {instance.user.username} ({instance.role})",
        )

    def perform_destroy(self, instance):
        # Deactivate the login (is_active=False), not a hard delete - same
        # "soft by default" reasoning Person.perform_destroy already uses:
        # this account may be the actor on old AuditLogEntry/EntryLog rows,
        # which should keep pointing at a real (if disabled) account rather
        # than a foreign key Django would otherwise null out.
        instance.user.is_active = False
        instance.user.save(update_fields=["is_active"])
        log_action(
            self.request.user, "account_deactivated",
            target_description=f"Account {instance.user.username} ({instance.role})",
        )

