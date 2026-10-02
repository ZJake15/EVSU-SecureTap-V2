import math

from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.response import Response
from rest_framework_simplejwt.views import TokenObtainPairView

from audit.utils import log_action
from configuration import store as system_settings

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
        lockout_on = system_settings.get("lockout_enabled")
        now = timezone.now()

        if lockout_on and profile and profile.locked_until and profile.locked_until > now:
            minutes = max(1, math.ceil((profile.locked_until - now).total_seconds() / 60))
            return Response(
                {"detail": f"Too many wrong passwords. This account is locked - try again in {minutes} "
                           f"minute{'s' if minutes != 1 else ''}."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            response = super().post(request, *args, **kwargs)
        except AuthenticationFailed:
            if lockout_on and profile:
                self._record_failure(profile, now)
            raise

        if profile and (profile.failed_login_count or profile.locked_until):
            profile.failed_login_count = 0
            profile.locked_until = None
            profile.save(update_fields=["failed_login_count", "locked_until"])
        return response

    @staticmethod
    def _record_failure(profile, now):
        attempts = system_settings.get("lockout_attempts")
        profile.failed_login_count += 1
        if profile.failed_login_count >= attempts:
            minutes = system_settings.get("lockout_minutes")
            profile.locked_until = now + timezone.timedelta(minutes=minutes)
            profile.failed_login_count = 0
            log_action(
                None, "account_locked",
                target_description=f"Account {profile.user.username}",
                detail={"wrong_passwords": attempts, "locked_for_minutes": minutes},
            )
        profile.save(update_fields=["failed_login_count", "locked_until"])


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

