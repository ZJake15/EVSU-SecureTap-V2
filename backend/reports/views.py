from datetime import timedelta

from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsAdminOrSaso
from logs.models import EntryLog
from users.threshold_eval import far_frr_table, leave_one_out_similarities

MAX_RANGE_DAYS = 90
DEFAULT_RANGE_DAYS = 7


def _parse_days(request):
    raw = request.query_params.get("days")
    try:
        days = int(raw) if raw else DEFAULT_RANGE_DAYS
    except ValueError:
        days = DEFAULT_RANGE_DAYS
    return max(1, min(days, MAX_RANGE_DAYS))


class SummaryView(APIView):
    """All the date/hour bucketing below is done in Python (fetch raw
    timestamps, group with timezone.localtime() per row) rather than with
    Django's TruncDate/TruncHour + a DB-side Count. TruncHour/TruncDate
    trigger a timezone-converting query on MySQL, which throws "Database
    returned an invalid datetime value - are time zone definitions for your
    database installed?" unless the server's mysql.time_zone* tables have
    been loaded (via mysql_tzinfo_to_sql) - not the case on this project's
    dev MySQL install. That 500 was the actual cause of the dashboard
    Reports page being stuck on "Loading report..." forever (the frontend
    fetch had no .catch(), so the failure was silently swallowed). Doing the
    grouping in Python sidesteps the DB-side timezone conversion entirely."""

    permission_classes = [IsAdminOrSaso]

    def get(self, request):
        now = timezone.localtime()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow_start = today_start + timedelta(days=1)

        today_logs = EntryLog.real.filter(timestamp__gte=today_start, timestamp__lt=tomorrow_start)
        # Everything below about "entries" counts people coming in only; a
        # gate with an exit camera also records people leaving, and those are
        # reported on their own (in_out, below) rather than counted twice.
        today_entries = today_logs.filter(direction=EntryLog.Direction.ENTRY)

        entries_today = today_entries.filter(status=EntryLog.Status.SUCCESS).count()
        exits_today = today_logs.filter(direction=EntryLog.Direction.EXIT, status=EntryLog.Status.SUCCESS).count()
        failed_today = today_logs.filter(status=EntryLog.Status.FAILED).count()

        entries_by_hour = self._counts_by_key(
            today_entries.filter(status=EntryLog.Status.SUCCESS).values_list("timestamp", flat=True),
            key_fn=lambda local_dt: local_dt.strftime("%H:00"),
            count_key="hour",
        )
        peak_hour = max(entries_by_hour, key=lambda item: item["count"])["hour"] if entries_by_hour else None

        week_start = today_start - timedelta(days=6)
        entries_by_day = self._counts_by_key(
            EntryLog.real.filter(
                timestamp__gte=week_start, timestamp__lt=tomorrow_start, status=EntryLog.Status.SUCCESS,
                direction=EntryLog.Direction.ENTRY,
            ).values_list("timestamp", flat=True),
            key_fn=lambda local_dt: local_dt.strftime("%Y-%m-%d"),
            count_key="date",
        )

        days = _parse_days(request)
        range_start = today_start - timedelta(days=days - 1)
        range_logs = EntryLog.real.filter(timestamp__gte=range_start, timestamp__lt=tomorrow_start)
        range_entries = range_logs.filter(direction=EntryLog.Direction.ENTRY)

        return Response(
            {
                "entries_today": entries_today,
                "exits_today": exits_today,
                "failed_today": failed_today,
                "peak_hour": peak_hour,
                "entries_by_hour": entries_by_hour,
                "entries_by_day": entries_by_day,
                "range_days": days,
                "entries_by_method": self._entries_by_method(range_entries),
                "entries_by_unit": self._entries_by_unit(range_entries),
                "confidence_histogram": self._confidence_histogram(range_logs),
                "busiest_hours": self._busiest_hours(range_entries),
                "in_out": self._in_out(range_logs),
            }
        )

    @staticmethod
    def _in_out(range_logs):
        """Entries against exits for the range: both per hour of the day, the
        busiest hour for leaving, how long people stayed (an exit paired with
        the same person's entry before it that day), the odd patterns
        (EntryLog.PatternFlag), and the unknown / spoof records at the exits."""
        hours = [{"hour": f"{hour:02d}:00", "entries": 0, "exits": 0} for hour in range(24)]
        stays = []
        open_entry = {}  # (person, local date) -> time of their entry still waiting for an exit
        let_in = range_logs.filter(status=EntryLog.Status.SUCCESS).order_by("timestamp")
        for person_id, direction, ts in let_in.values_list("person_id", "direction", "timestamp"):
            local = timezone.localtime(ts)
            hours[local.hour]["entries" if direction == EntryLog.Direction.ENTRY else "exits"] += 1
            if person_id is None:
                continue
            key = (person_id, local.date())
            if direction == EntryLog.Direction.ENTRY:
                open_entry[key] = ts
            elif key in open_entry:
                stays.append((ts - open_entry.pop(key)).total_seconds() / 60)
        stays.sort()
        exits = range_logs.filter(direction=EntryLog.Direction.EXIT)
        busiest_exit = max(hours, key=lambda row: row["exits"])
        flags = dict.fromkeys(("no_entry", "no_exit"), 0)
        for flag in range_logs.exclude(pattern_flag="").values_list("pattern_flag", flat=True):
            flags[flag] = flags.get(flag, 0) + 1
        return {
            "by_hour": hours,
            "entries": sum(row["entries"] for row in hours),
            "exits": sum(row["exits"] for row in hours),
            "busiest_exit_hour": busiest_exit["hour"] if busiest_exit["exits"] else None,
            "stays": len(stays),
            "average_stay_minutes": round(sum(stays) / len(stays)) if stays else None,
            "median_stay_minutes": round(stays[len(stays) // 2]) if stays else None,
            "pattern_flags": flags,
            "exit_unknown": exits.filter(status=EntryLog.Status.FAILED).count(),
            "exit_spoof": exits.filter(status=EntryLog.Status.SPOOF_SUSPECTED).count(),
        }

    @staticmethod
    def _counts_by_key(timestamps, key_fn, count_key):
        """Groups a queryset of raw timestamps into {count_key, count} rows,
        keyed by key_fn(local_datetime) - only keys that actually occur are
        included (matches the sparse shape the old TruncDate/TruncHour
        annotation produced), sorted by key."""
        counts = {}
        for ts in timestamps:
            key = key_fn(timezone.localtime(ts))
            counts[key] = counts.get(key, 0) + 1
        return [{count_key: key, "count": counts[key]} for key in sorted(counts)]

    @staticmethod
    def _entries_by_method(range_logs):
        """Per-day counts split into the states shown on Live Monitoring's
        method badges: a clean face match, a face match resolved by an NFC
        tiebreak tap, a failed/unrecognized attempt, or an occluded attempt.
        occlusion_detected gets its own bucket rather than folding into
        "failed" - silently doing that would mean this chart quietly
        disagreed with the dashboard's own status filter/badge, which treats
        it as a distinct outcome (see EntryLog.Status.OCCLUSION_DETECTED)."""
        # Every outcome has a bucket, so the chart adds up to everything the
        # gates logged: successful entries by how they were confirmed (face,
        # face + card - an ambiguous match or a lookalike resolved by a tap -
        # card alone, a guard's manual override), then each kind of refusal.
        success_bucket = {
            EntryLog.VerificationMethod.FACE_ONLY: "face",
            EntryLog.VerificationMethod.FACE_AND_CARD_TIEBREAK: "face_nfc",
            EntryLog.VerificationMethod.CONFUSABLE_PAIR_TIEBREAK: "face_nfc",
            EntryLog.VerificationMethod.MANUAL_OVERRIDE: "manual",
        }
        status_bucket = {
            EntryLog.Status.OCCLUSION_DETECTED: "occluded",
            EntryLog.Status.FAILED: "failed",
            EntryLog.Status.SPOOF_SUSPECTED: "spoof",
            EntryLog.Status.NOT_ENROLLED: "not_enrolled",
        }
        empty = {"face": 0, "face_nfc": 0, "card": 0, "manual": 0, "failed": 0, "spoof": 0, "occluded": 0,
                 "not_enrolled": 0}
        by_date = {}
        for ts, method, status in range_logs.values_list("timestamp", "verification_method", "status"):
            date_key = timezone.localtime(ts).strftime("%Y-%m-%d")
            bucket = by_date.setdefault(date_key, dict(empty))
            if status == EntryLog.Status.SUCCESS:
                bucket[success_bucket.get(method, "card")] += 1  # NFC only, or an old NFC + face row
            elif status in status_bucket:
                bucket[status_bucket[status]] += 1
        return [{"date": date_key, **counts} for date_key, counts in sorted(by_date.items())]

    @staticmethod
    def _entries_by_unit(range_logs):
        """Successful entries in the range by the school or office of whoever
        entered, most first - students and staff counted apart. People
        registered before schools/offices were a list count as "Not set"."""
        rows = {}
        for unit, kind, role in range_logs.filter(status=EntryLog.Status.SUCCESS, person__isnull=False).values_list(
                "person__org_unit__name", "person__org_unit__kind", "person__role"):
            key = unit or "Not set"
            row = rows.setdefault(key, {"unit": key, "kind": kind or "", "students": 0, "staff": 0, "total": 0})
            row["students" if role == "student" else "staff"] += 1
            row["total"] += 1
        return sorted(rows.values(), key=lambda row: (-row["total"], row["unit"]))

    @staticmethod
    def _confidence_histogram(range_logs):
        """match_confidence (0-1 cosine similarity) for successful matches
        in range, bucketed into 10%-wide bins - shows how cleanly the
        threshold separates confident matches from borderline ones."""
        buckets = [0] * 10
        confidences = range_logs.filter(
            status=EntryLog.Status.SUCCESS, match_confidence__isnull=False
        ).values_list("match_confidence", flat=True)
        for confidence in confidences:
            index = min(9, max(0, int(confidence * 10)))
            buckets[index] += 1
        return [{"bucket": f"{i * 10}-{i * 10 + 10}%", "count": buckets[i]} for i in range(10)]

    @staticmethod
    def _busiest_hours(range_logs):
        """Successful-entry counts by hour-of-day (0-23), summed across the
        whole selected range - not the same as entries_by_hour above, which
        is today only. Bucketed in Python (via localtime per row) rather
        than a DB-side hour extraction, to sidestep timezone-handling
        differences across database backends."""
        hour_counts = [0] * 24
        timestamps = range_logs.filter(status=EntryLog.Status.SUCCESS).values_list("timestamp", flat=True)
        for ts in timestamps:
            hour_counts[timezone.localtime(ts).hour] += 1
        return [{"hour": f"{hour:02d}:00", "count": hour_counts[hour]} for hour in range(24)]


class FarFrrView(APIView):
    """False-accept/false-reject rates across a range of candidate similarity
    thresholds, for the Reports page. Leave-one-out over currently enrolled
    photos (same computation as the evaluate_threshold management command,
    via users.threshold_eval) - not a held-out test set, since none exists
    yet. Labeled as such in the response so the dashboard can show the
    caveat rather than presenting it as a rigorous accuracy figure."""

    permission_classes = [IsAdminOrSaso]

    def get(self, request):
        same_person, cross_person = leave_one_out_similarities()
        table = far_frr_table(same_person, cross_person)
        return Response(
            {
                "same_person_count": len(same_person),
                "cross_person_count": len(cross_person),
                "table": table,
                "note": (
                    "Leave-one-out estimate over currently enrolled photos, not a held-out test "
                    "set - treat as a preliminary figure."
                ),
            }
        )
