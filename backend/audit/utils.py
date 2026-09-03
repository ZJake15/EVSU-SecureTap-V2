from .models import AuditLogEntry


def log_action(actor, action, target_description="", detail=None):
    """The one place an AuditLogEntry gets created - called from accounts/
    users/logs views for the specific actions the brief requires logged
    (account changes, deactivation requests/approvals/rejections, manual
    overrides, permanent deletes). Never raises on a bad `action` value the
    same way normal code wouldn't - it's a plain field write, not user input -
    so a typo here is a programming error to catch in review, not something
    to defend against at runtime."""
    AuditLogEntry.objects.create(
        actor=actor, action=action, target_description=target_description, detail=detail,
    )
