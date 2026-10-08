"""Failed login lockout (Settings page, off by default) - shared by the
dashboard sign-in (accounts/views.py) and the gate monitor's guard sign-in
(logs/gate_shifts.py), so wrong passwords count the same wherever they're
typed."""

import math

from django.utils import timezone

from audit.utils import log_action
from configuration import store as system_settings


def locked_message(profile, now=None):
    """The "try again in N minutes" message while this account is locked,
    else None."""
    now = now or timezone.now()
    if not (system_settings.get("lockout_enabled") and profile and profile.locked_until
            and profile.locked_until > now):
        return None
    minutes = max(1, math.ceil((profile.locked_until - now).total_seconds() / 60))
    return f"Too many wrong passwords. This account is locked - try again in {minutes} minute{'s' if minutes != 1 else ''}."


def record_failure(profile, now=None):
    """Counts one wrong password; locks the account once there are too many
    in a row."""
    if not (system_settings.get("lockout_enabled") and profile):
        return
    now = now or timezone.now()
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


def clear_failures(profile):
    """A correct password resets the count."""
    if profile and (profile.failed_login_count or profile.locked_until):
        profile.failed_login_count = 0
        profile.locked_until = None
        profile.save(update_fields=["failed_login_count", "locked_until"])
