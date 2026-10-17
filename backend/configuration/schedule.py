"""The Settings page's Scheduling section, as answers to "when?":

- the clean-up's timetable (cleanup_frequency/_weekday/_month_day/_time):
  when it was last due and when it's next due - purge_old_data --if-due
  runs it once each due moment has passed, and the Settings page shows
  the last and next run;
- the gate's opening hours (gate_hours_enabled/_open_time/_close_time/
  _open_days): whether a moment is outside them - logs.models.EntryLog
  tags such an entry After hours.

All in the backend's local time (settings.TIME_ZONE).
"""

from datetime import datetime, time, timedelta

from django.utils import timezone

from . import store
from .models import ScheduledJob
from .registry import WEEKDAYS

# Weekday keys in datetime.weekday() order (Monday = 0).
WEEKDAY_KEYS = [key for key, _label in WEEKDAYS]
# Far enough either side of today to find a monthly moment (day 28 at most).
_SEARCH_DAYS = 62

CLEANUP_JOB = "cleanup_retention"  # the deletions Automatic deletion controls
ALWAYS_JOB = "cleanup_always"  # unknown-face data, leftover files - every run


def parse_time(text):
    hours, minutes = (int(part) for part in text.split(":"))
    return time(hours, minutes)


def _is_cleanup_day(day):
    frequency = store.get("cleanup_frequency")
    if frequency == "weekly":
        return WEEKDAY_KEYS[day.weekday()] == store.get("cleanup_weekday")
    if frequency == "monthly":
        return day.day == store.get("cleanup_month_day")
    return True  # daily


def _cleanup_moments(around):
    at = parse_time(store.get("cleanup_time"))
    today = around.date()
    for offset in range(-_SEARCH_DAYS, _SEARCH_DAYS + 1):
        day = today + timedelta(days=offset)
        if _is_cleanup_day(day):
            yield timezone.make_aware(datetime.combine(day, at))


def last_cleanup_moment(now=None):
    """The most recent time the clean-up was due, at or before `now`."""
    now = timezone.localtime(now or timezone.now())
    return max((moment for moment in _cleanup_moments(now) if moment <= now), default=None)


def next_cleanup_moment(now=None):
    """The next time the clean-up is due, after `now`."""
    now = timezone.localtime(now or timezone.now())
    return min((moment for moment in _cleanup_moments(now) if moment > now), default=None)


def job(name):
    return ScheduledJob.objects.get_or_create(name=name)[0]


def cleanup_due(now=None):
    """True once a due moment has passed since the clean-up last ran - so a
    computer that was off at 02:00 catches up the next time it's on. Never
    run before counts as due."""
    last_run = job(CLEANUP_JOB).last_run_at
    due_at = last_cleanup_moment(now)
    return last_run is None or (due_at is not None and last_run < due_at)


def is_after_hours(moment=None):
    """Whether `moment` (default now) is outside the gate's opening hours.
    Always False with Gate opening hours off. A closing time before the
    opening time means open overnight: 01:00 after an 18:00-02:00 Friday
    belongs to Friday. The same opening and closing time means open all
    day on the open days."""
    if not store.get("gate_hours_enabled"):
        return False
    local = timezone.localtime(moment or timezone.now())
    opens, closes = parse_time(store.get("gate_open_time")), parse_time(store.get("gate_close_time"))
    open_days = {key for key in store.get("gate_open_days").split(",") if key}
    now_time = local.time()

    def open_on(day):
        return WEEKDAY_KEYS[day.weekday()] in open_days

    if opens == closes:
        is_open = open_on(local)
    elif opens < closes:
        is_open = open_on(local) and opens <= now_time < closes
    elif now_time >= opens:
        is_open = open_on(local)
    elif now_time < closes:
        is_open = open_on(local - timedelta(days=1))
    else:
        is_open = False
    return not is_open


def describe_cleanup(now=None):
    """The Settings page's live line under the clean-up time: when it last
    ran and what it deleted, and when it runs next."""
    if not store.get("auto_delete_enabled"):
        return "Automatic deletion is off, so nothing is deleted on this schedule."
    record = job(CLEANUP_JOB)
    parts = []
    if record.last_run_at:
        counts = record.last_result or {}
        deleted = ", ".join(
            f"{counts.get(key, 0)} {label}" for key, label in (
                ("entry_records", "entry records"), ("gate_photos", "gate photos"), ("audit_entries", "audit entries"),
            )
        )
        parts.append(f"Last clean-up: {_when(record.last_run_at)} - deleted {deleted}.")
    else:
        parts.append("Not run yet.")
    upcoming = next_cleanup_moment(now)
    if upcoming:
        parts.append(f"Next: {_when(upcoming)}.")
    return " ".join(parts)


def _when(moment):
    return timezone.localtime(moment).strftime("%b %d, %Y, %H:%M")
