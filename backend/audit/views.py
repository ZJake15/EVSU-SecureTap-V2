from rest_framework import viewsets

from accounts.models import AdminProfile
from accounts.permissions import IsAdminOrOwnAuditEntries, get_role

from .models import AuditLogEntry
from .serializers import AuditLogEntrySerializer


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    """Admin sees every entry; SASO sees only rows where they were the actor;
    Security Officer can't reach this endpoint at all (see
    IsAdminOrOwnAuditEntries) - they aren't audited on anything but manual
    overrides, which already show up distinctly in Logs."""

    serializer_class = AuditLogEntrySerializer
    permission_classes = [IsAdminOrOwnAuditEntries]

    def get_queryset(self):
        queryset = AuditLogEntry.objects.select_related("actor").all()
        if get_role(self.request.user) == AdminProfile.Role.SASO:
            queryset = queryset.filter(actor=self.request.user)
        return queryset
