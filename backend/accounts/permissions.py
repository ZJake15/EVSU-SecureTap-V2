from django.conf import settings
from rest_framework.permissions import BasePermission

from .models import AdminProfile

ADMIN = AdminProfile.Role.ADMIN
SASO = AdminProfile.Role.SASO
SECURITY_OFFICER = AdminProfile.Role.SECURITY_OFFICER


def get_role(user):
    if not user or not user.is_authenticated:
        return None
    try:
        return user.admin_profile.role
    except AdminProfile.DoesNotExist:
        return None


def get_assigned_gate(user):
    """A Security Officer's assigned gate, or None if the account has no
    profile, isn't a Security Officer, or the field is blank. Callers use
    None to mean "can't determine a gate" and should fail closed (show
    nothing) rather than accidentally show every gate."""
    if not user or not user.is_authenticated:
        return None
    try:
        profile = user.admin_profile
    except AdminProfile.DoesNotExist:
        return None
    if profile.role != SECURITY_OFFICER:
        return None
    return profile.assigned_gate_location or None


class IsAdmin(BasePermission):
    """Admin-only: account management, system settings, deactivation-request
    approval/rejection, permanent deletes."""

    def has_permission(self, request, view):
        return get_role(request.user) == ADMIN


class IsAdminOrSaso(BasePermission):
    """User Management (enroll/edit/bulk-import) and Reports - both roles get
    full access; a Security Officer gets neither, per the access matrix."""

    def has_permission(self, request, view):
        return get_role(request.user) in (ADMIN, SASO)


class IsSecurityOfficer(BasePermission):
    """Manual override is scoped to Security Officer only, per the brief -
    it's specifically framed as a gate operator's judgment call when the
    scanner fails. Admin/SASO aren't blocked from anything by this (they
    have full visibility into the resulting log either way), they just don't
    get this specific action - keeping it exclusively "a guard's call, not
    the system's" is the whole point of logging it separately at all."""

    def has_permission(self, request, view):
        return get_role(request.user) == SECURITY_OFFICER


class IsAnyDashboardRole(BasePermission):
    """Any of the three roles - used for endpoints every logged-in dashboard
    account can reach at all (Live Monitoring, Logs), where the *scope* of
    what they see (all gates vs. their own, full history vs. today) is
    narrowed at the queryset level instead of by blocking the endpoint
    outright - see logs/views.py."""

    def has_permission(self, request, view):
        return get_role(request.user) in (ADMIN, SASO, SECURITY_OFFICER)


class IsAdminOrOwnAuditEntries(BasePermission):
    """Audit Log: Admin sees everything, SASO sees only their own actions,
    Security Officer gets no access at all (not even their own - they have no
    actions to view since they're not audited on anything but manual
    overrides, which already show up distinctly in Logs). The has_permission
    check here only gates entry to the endpoint at all (admin/saso) - the
    "only own actions" narrowing for SASO happens in the queryset, the same
    scoping pattern IsAnyDashboardRole's callers use."""

    def has_permission(self, request, view):
        return get_role(request.user) in (ADMIN, SASO)


class HasServiceToken(BasePermission):
    """Authenticates the entry-agent via a shared-secret header instead of a user JWT."""

    def has_permission(self, request, view):
        token = request.headers.get("X-Service-Token")
        return bool(token) and token == settings.ENTRY_AGENT_SERVICE_TOKEN
