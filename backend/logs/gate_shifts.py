"""Guards on duty at a gate - Settings -> "Guards sign in at the gate monitor".

A guard signs in at a gate monitor with their password or a tap of their own
staff ID card (AdminProfile.staff_card_id); that starts a GateShift. Every
entry written at that gate while the shift lasts records the guard as
on_duty; with sign-in switched on and nobody signed in, entries are marked
unattended instead - the gate itself never stops scanning. A shift ends when
the guard signs out, another guard signs in at the same gate, the gate
monitor closes or reopens, or after the shift time limit (Settings).

Who may sign in: a Security Officer only at their assigned gate (the same
exact-name match their Logs access uses); an Admin or SASO at any gate.
"""

from django.db import transaction
from django.utils import timezone

from accounts.models import AdminProfile
from audit.utils import log_action
from configuration import store as system_settings

from .models import GateShift


def enabled():
    return system_settings.get("gate_sign_in_enabled")


def active_shift(gate_location):
    """The shift on duty at this gate right now, or None. A shift past the
    time limit is closed here, the moment anything asks."""
    shift = (GateShift.objects.select_related("user").filter(gate_location=gate_location, ended_at__isnull=True)
             .order_by("-signed_in_at").first())
    if shift is None:
        return None
    limit = timezone.timedelta(hours=system_settings.get("gate_shift_hours"))
    if timezone.now() - shift.signed_in_at >= limit:
        end_shift(shift, GateShift.EndReason.EXPIRED, ended_at=shift.signed_in_at + limit)
        return None
    return shift


def stamp_on_duty(entry_log):
    """Called by EntryLog.save() for a new row: who was on duty, or
    unattended. Leaves both empty while gate sign-in is off."""
    if not enabled():
        return
    shift = active_shift(entry_log.gate_location)
    if shift is not None and shift.user_id is not None:
        entry_log.on_duty_id = shift.user_id
    else:
        entry_log.unattended = True


def refusal(user, gate_location):
    """Why this account may not sign in at this gate, or None if it may."""
    if user is None or not user.is_active:
        return "This account is deactivated."
    try:
        profile = user.admin_profile
    except AdminProfile.DoesNotExist:
        return "This account has no SecureTap role."
    if profile.role == AdminProfile.Role.SECURITY_OFFICER and profile.assigned_gate_location != gate_location:
        assigned = profile.assigned_gate_location or "no gate"
        return f"This Security Officer is assigned to {assigned}, not {gate_location}."
    return None


def start_shift(user, gate_location, method):
    """Signs `user` in at this gate, ending whoever was on duty there. The
    same guard signing in again keeps their running shift."""
    with transaction.atomic():
        current = active_shift(gate_location)
        if current is not None and current.user_id == user.id:
            return current
        for shift in GateShift.objects.select_for_update().filter(gate_location=gate_location,
                                                                  ended_at__isnull=True):
            end_shift(shift, GateShift.EndReason.REPLACED)
        shift = GateShift.objects.create(user=user, gate_location=gate_location, method=method)
    log_action(
        user, "gate_sign_in", target_description=f"{gate_location} ({shift.get_method_display().lower()})",
        detail={"gate_location": gate_location, "method": method, "shift_id": shift.id},
    )
    return shift


def end_shift(shift, reason, ended_at=None):
    shift.ended_at = ended_at or timezone.now()
    shift.end_reason = reason
    shift.save(update_fields=["ended_at", "end_reason"])
    minutes = round((shift.ended_at - shift.signed_in_at).total_seconds() / 60)
    log_action(
        shift.user, "gate_sign_out",
        target_description=f"{shift.gate_location} ({GateShift.EndReason(reason).label.lower()})",
        detail={"gate_location": shift.gate_location, "reason": reason, "shift_id": shift.id,
                "minutes_on_duty": minutes},
    )


def end_shifts_at(gate_location, reason):
    """Ends every open shift at this gate (the gate monitor closing or
    reopening)."""
    with transaction.atomic():
        for shift in GateShift.objects.select_for_update().filter(gate_location=gate_location,
                                                                  ended_at__isnull=True):
            end_shift(shift, reason)


def status(gate_location):
    """What the gate monitor shows: is sign-in on, and who's on duty."""
    if not enabled():
        return {"enabled": False, "on_duty": None}
    shift = active_shift(gate_location)
    on_duty = None
    if shift is not None and shift.user is not None:
        user = shift.user
        on_duty = {
            "shift_id": shift.id,
            "name": user.get_full_name() or user.username,
            "username": user.username,
            "role": getattr(getattr(user, "admin_profile", None), "role", None),
            "since": timezone.localtime(shift.signed_in_at).isoformat(timespec="seconds"),
            "method": shift.method,
        }
    return {"enabled": True, "on_duty": on_duty}
