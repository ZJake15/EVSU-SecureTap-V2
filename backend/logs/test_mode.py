"""The capstone speed/accuracy test mode - the backend's side.

The gate monitor in test mode (entry-agent/test_mode.py) sends test_mode=1
with each frame. That changes NO decision: every frame goes through exactly
the same checks, thresholds and voting as normal operation. It only:

- marks every entry written while handling that frame as a test entry
  (EntryLog.is_test - set in EntryLog.save from `active()`), which the
  dashboard's Logs, Live Monitoring, Reports, gate counters and duty board
  leave out (they read EntryLog.real);
- remembers when and at which gate test frames arrived (TestSession), so
  "delete all test data" can also remove the short-lived face data the
  scan kept while testing - including an unregistered volunteer's;
- adds measurement-only fields to the answer (which quality check skipped a
  frame, each face's estimated distance) - see IdentifyView.

delete_test_data() removes all of it: test entries (their photos go with
them - users/photo_files.py), the scan's working rows at those gates during
those sessions, and the sessions. It never touches a real entry.
"""

import contextlib
import contextvars

from django.db import transaction
from django.utils import timezone

_active = contextvars.ContextVar("securetap_test_mode", default=False)

# A test frame this long after the last one starts a new session row.
SESSION_GAP = timezone.timedelta(minutes=10)
# Margins around a session when deleting the scan's working rows, so the
# first and last frames' rows are surely inside.
DELETE_MARGIN = timezone.timedelta(minutes=1)


def active():
    """Whether the frame being handled right now came from test mode."""
    return _active.get()


@contextlib.contextmanager
def marking(enabled, gate_location):
    """While handling one frame: everything written is a test entry if
    `enabled`. Also records the test session's time span at the gate."""
    if enabled:
        _note_session(gate_location)
    token = _active.set(bool(enabled))
    try:
        yield
    finally:
        _active.reset(token)


def _note_session(gate_location):
    from .models import TestSession

    now = timezone.now()
    session = (
        TestSession.objects.filter(gate_location=gate_location, last_seen__gte=now - SESSION_GAP)
        .order_by("-last_seen")
        .first()
    )
    if session is None:
        TestSession.objects.create(gate_location=gate_location, first_seen=now, last_seen=now)
    else:
        session.last_seen = now
        session.save(update_fields=["last_seen"])


def delete_test_data(actor=None):
    """Deletes every test entry and the scan's working data from test
    sessions. Returns what was deleted, by kind."""
    from audit.utils import log_action

    from .models import (
        EntryLog, OcclusionAttempt, PendingTiebreak, RecognitionAttempt, SpoofAttempt, TestSession, UnmatchedAttempt,
    )

    counts = {}
    with transaction.atomic():
        test_entries = EntryLog.objects.filter(is_test=True)
        counts["test_entries"] = test_entries.count()
        for entry in test_entries.iterator():
            entry.delete()  # one by one, so each photo file goes too (users/photo_files.py)
        working = {"unmatched_faces": UnmatchedAttempt, "spoof_attempts": SpoofAttempt,
                   "covered_frames": OcclusionAttempt, "match_votes": RecognitionAttempt}
        for key in working:
            counts[key] = 0
        counts["card_tap_requests"] = 0
        sessions = list(TestSession.objects.all())
        for session in sessions:
            window = (session.first_seen - DELETE_MARGIN, session.last_seen + DELETE_MARGIN)
            for key, model in working.items():
                counts[key] += model.objects.filter(gate_location=session.gate_location,
                                                    timestamp__range=window).delete()[0]
            counts["card_tap_requests"] += PendingTiebreak.objects.filter(
                gate_location=session.gate_location, created_at__range=window).delete()[0]
        counts["test_sessions"] = len(sessions)
        TestSession.objects.all().delete()
    log_action(actor, "test_data_deleted", target_description="Capstone test mode data", detail=counts)
    return counts
