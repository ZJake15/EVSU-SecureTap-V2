from django.conf import settings
from django.db import models


class AuditLogEntry(models.Model):
    """A permanent record of who did what and when, for the actions the
    access-control brief specifically calls out as needing one: account
    changes, settings changes, deactivation requests/approvals/rejections, and
    manual overrides.

    Deliberately a single generic model rather than a separate log per action
    type - Admin's "see everything" view and SASO's "see only my own actions"
    view are both just one filtered query against one table, and every new
    audited action going forward is one more `log_action()` call, not a new
    model/migration.

    Not used for every read or routine action (a SASO viewing Reports, an
    Admin listing Person records) - only for the specific state-changing
    actions the brief lists. See audit/utils.py's log_action() for the one
    place entries are created.
    """

    class Action(models.TextChoices):
        # Account management (Admin-only actions on other dashboard logins).
        ACCOUNT_CREATED = "account_created", "Account created"
        ACCOUNT_UPDATED = "account_updated", "Account updated"
        ACCOUNT_DEACTIVATED = "account_deactivated", "Account deactivated"
        # Deactivation maker-checker (see users.models.DeactivationRequest).
        DEACTIVATION_REQUESTED = "deactivation_requested", "Deactivation requested"
        DEACTIVATION_APPROVED = "deactivation_approved", "Deactivation approved"
        DEACTIVATION_REJECTED = "deactivation_rejected", "Deactivation rejected"
        # A guard's manual override entry (see logs.models.EntryLog.performed_by).
        MANUAL_OVERRIDE = "manual_override", "Manual override logged"
        # Person permanent delete is the one irreversible, Admin-only action
        # in User Management - worth a permanent record even though ordinary
        # enroll/edit/deactivate-request aren't individually audited (those
        # already show up in Person's own history/EntryLog, and auditing
        # every single edit would bury the actions that actually matter).
        PERSON_DELETED_PERMANENTLY = "person_deleted_permanently", "Person deleted permanently"
        # A confusable-pair flag/unflag (see users.models.ConfusablePair) -
        # worth its own record for the same reason a permanent delete is:
        # it's a deliberate, standing change to how the gate scan treats two
        # specific people going forward, not routine enroll/edit noise.
        CONFUSABLE_PAIR_FLAGGED = "confusable_pair_flagged", "Confusable pair flagged"
        CONFUSABLE_PAIR_REMOVED = "confusable_pair_removed", "Confusable pair removed"

    # Nullable only in case a user account is later deleted out from under an
    # old entry (SET_NULL, not CASCADE) - the row (and its description of what
    # happened) should survive even if the actor's login doesn't.
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="audit_entries"
    )
    action = models.CharField(max_length=32, choices=Action.choices)
    # Human-readable summary of what was acted on, e.g. "Person #12 (Juan Dela
    # Cruz)" or "Account jsmith_saso" - kept as plain text rather than a
    # generic FK/content-type relation (Django's contenttypes framework) since
    # the audited actions span a handful of unrelated models and a plain
    # description reads directly in the UI with no extra joins.
    target_description = models.CharField(max_length=255, blank=True)
    # Free-form structured detail specific to the action (e.g. a
    # deactivation's reason, or which fields changed) - optional, since not
    # every action has extra detail worth keeping.
    detail = models.JSONField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self):
        who = self.actor.username if self.actor else "(deleted account)"
        return f"{who} - {self.action} - {self.target_description} @ {self.timestamp}"
