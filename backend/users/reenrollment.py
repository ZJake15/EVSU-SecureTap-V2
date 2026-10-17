"""Face re-enrollment reminders (Settings > Scheduling): whose face photos
are due to be retaken, worked out from what's already stored - nothing new
is saved.

- Photos due: the newest face photo is older than "Ask for new face photos
  every" (reenroll_months; 0 = off), counted in calendar months.
- Due soon: within "Remind before it's due" (reenroll_remind_days) of that.
- Matches getting weak (reenroll_weak_matches): the middle score of the
  person's last WEAK_RECENT_ENTRIES face entries since their newest photos
  is under Match strictness + WEAK_MARGIN, over at least WEAK_MIN_ENTRIES
  entries. Measured on this system: successful face matches score a median
  of 0.67 and 95% above 0.52, against a match bar of 0.45 - so a person
  whose typical match sits under 0.55 is drifting toward being read as
  Unknown.

Retaking the photos (User Management > Retake face photos) clears all three:
the newest photo is then today's, and only entries after it count.
Re-enrollment never blocks anyone at the gate - it's a reminder only.
"""

import calendar
import statistics
from datetime import timedelta

from django.utils import timezone

from configuration import store

DUE, WEAK, DUE_SOON = "due", "weak_matches", "due_soon"
WEAK_RECENT_ENTRIES = 10
WEAK_MIN_ENTRIES = 5
WEAK_MARGIN = 0.10


def photos_taken_at(person):
    """When the person's newest face photo was saved, or None without any."""
    dates = [embedding.created_at for embedding in person.face_embeddings.all()]
    return max(dates) if dates else None


def _add_months(moment, months):
    month_index = moment.month - 1 + months
    year, month = moment.year + month_index // 12, month_index % 12 + 1
    return moment.replace(year=year, month=month, day=min(moment.day, calendar.monthrange(year, month)[1]))


def _weak_matches(person, since):
    from logs.models import EntryLog

    scores = list(
        EntryLog.objects.filter(
            person=person, status=EntryLog.Status.SUCCESS, verification_method=EntryLog.VerificationMethod.FACE_ONLY,
            timestamp__gte=since, match_confidence__isnull=False,
        ).order_by("-timestamp").values_list("match_confidence", flat=True)[:WEAK_RECENT_ENTRIES]
    )
    return (
        len(scores) >= WEAK_MIN_ENTRIES
        and statistics.median(scores) < store.get("match_strictness") + WEAK_MARGIN
    )


def status(person, now=None):
    """{"status": DUE | WEAK | DUE_SOON | None, "photos_taken_at", "due_at"}.
    DUE and WEAK mean "retake the photos now"; DUE_SOON is the heads-up."""
    now = now or timezone.now()
    taken = photos_taken_at(person)
    result = {"status": None, "photos_taken_at": taken, "due_at": None}
    if taken is None:
        return result  # nothing enrolled yet - nothing to retake
    months = store.get("reenroll_months")
    if months:
        result["due_at"] = _add_months(taken, months)
        if now >= result["due_at"]:
            result["status"] = DUE
            return result
    if store.get("reenroll_weak_matches") and _weak_matches(person, taken):
        result["status"] = WEAK
    elif result["due_at"] and now >= result["due_at"] - timedelta(days=store.get("reenroll_remind_days")):
        result["status"] = DUE_SOON
    return result


def photos_due(person):
    """For the gate monitor's "Photos due for update" note."""
    return status(person)["status"] in (DUE, WEAK)
