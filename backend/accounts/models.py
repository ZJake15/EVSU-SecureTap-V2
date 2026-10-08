from django.conf import settings
from django.db import models


class AdminProfile(models.Model):
    """Extends Django's built-in User with the dashboard role and (for a
    Security Officer) their assigned gate.

    This stands in for the spec's "admin_accounts" table: rather than a hand-rolled
    username/password_hash table, we reuse Django's battle-tested auth_user (hashed
    with bcrypt - see PASSWORD_HASHERS in settings.py) and just attach a role to it.

    Three roles, a deliberate hierarchy from broadest to narrowest:
    - ADMIN: full control - everything below, plus account management, system
      settings, and approving/rejecting SASO deactivation requests.
    - SASO (Security Manager): oversight and enrollment - full Live Monitoring/
      Logs/Reports, can enroll and edit Person records, but deactivating one
      only creates a DeactivationRequest for an Admin to resolve (see
      users.models.DeactivationRequest) rather than taking effect immediately.
    - SECURITY_OFFICER: real-time gate operation only - view-only Live
      Monitoring/Logs scoped to their own assigned_gate_location (enforced
      server-side in logs/views.py, not just hidden in the UI), plus the
      ability to log a manual override when the scanner itself fails.

    The previous "security"/"it" roles are gone as of this migration: "it" had
    identical permissions to "admin" in practice (same nav, same API access -
    only Django's is_superuser flag differed) and folded into it; "security"
    already behaved close to today's SECURITY_OFFICER and was renamed to it
    directly (see the migration that introduces this Role change for the exact
    data migration).
    """

    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        SASO = "saso", "Security Manager (SASO)"
        SECURITY_OFFICER = "security_officer", "Security Officer"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="admin_profile"
    )
    role = models.CharField(max_length=20, choices=Role.choices)
    # Only meaningful for SECURITY_OFFICER - which gate their Live Monitoring/
    # Logs access is scoped to (see logs/views.py). Blank for Admin/SASO, who
    # aren't gate-scoped at all.
    #
    # Free-text, matched by exact string against EntryLog.gate_location -
    # consistent with how gate_location is represented everywhere else in this
    # codebase (there is no separate Gate/Device registry model - see
    # documentation.md's data-model notes). This is a real, known fragility:
    # a typo here, or drift against whatever gate_location string an
    # entry-agent instance is actually configured with, silently mismatches
    # rather than erroring - there's nothing to validate against. Introducing
    # a formal Gate model would fix that, but is a bigger change than this
    # access-control pass scoped for; flagged here as a documented follow-up,
    # not silently worked around.
    assigned_gate_location = models.CharField(max_length=100, blank=True)
    # The staff member's own ID card, for signing in at the gate monitor with
    # one tap (Settings -> "Guards sign in at the gate monitor"). Blank means
    # password sign-in only. Never the same number as a student's card -
    # checked in DashboardAccountSerializer and PersonSerializer.
    staff_card_id = models.CharField(max_length=100, blank=True, default="", db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # Failed login lockout (Settings page, off by default): wrong passwords
    # in a row since the last successful sign-in, and when a lock ends.
    failed_login_count = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.user.username} ({self.role})"
