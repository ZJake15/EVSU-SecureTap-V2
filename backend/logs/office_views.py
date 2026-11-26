"""The security office's view of the whole system, on the dashboard.

AlarmsView: the office alarm - a suspected fake face, or the same unknown
person seen again, pops up with a sound on whatever dashboard page is open
(dashboard/src/components/OfficeAlarms.jsx asks every few seconds).

SystemStatusView: the System Status page - is every gate monitor running,
its cameras and offline queue, who's on duty, and the server's last backup,
free disk space and database size, with plain-words warnings on top.
"""

import shutil
from pathlib import Path

from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsAdminOrSaso
from audit.models import AuditLogEntry
from configuration import store as system_settings
from users.models import Person

from . import camera_status, gate_shifts, lanes
from .models import EntryLog, GateCamera
from .serializers import EntryLogSerializer

# The most alarms one answer carries - a burst beyond that says "more".
ALARMS_PER_ANSWER = 20
# A tab that was asleep a long time gets only the last few minutes' alarms -
# the rest are in Logs, not a flood of pop-ups.
ALARMS_LOOK_BACK = timezone.timedelta(minutes=10)


def alarm_kinds():
    """Which events pop up: Settings > Alerts, each with its gate-side
    switch, and "Office alarm on the dashboard" over both."""
    if not system_settings.get("alert_dashboard"):
        return set()
    kinds = set()
    if system_settings.get("alert_spoof"):
        kinds.add("spoof")
    if system_settings.get("alert_repeated_unknown"):
        kinds.add("repeated_unknown")
    return kinds


class AlarmsView(APIView):
    """GET ?since=<server_time from the last answer>: the alarms since then,
    oldest first, and the time to ask from next. The first ask (no since)
    only gets the time - nothing from before the page opened pops up.
    Admin and SASO only."""

    permission_classes = [IsAdminOrSaso]

    def get(self, request):
        now = timezone.now()
        since = parse_datetime(request.query_params.get("since") or "")
        kinds = alarm_kinds()
        answer = {"server_time": now.isoformat(), "enabled": bool(kinds), "alarms": [], "more": False}
        if since is None or not kinds:
            return Response(answer)
        if timezone.is_naive(since):
            since = timezone.make_aware(since)
        since = max(since, now - ALARMS_LOOK_BACK)
        rows = EntryLog.real.select_related("person", "on_duty").filter(timestamp__gt=since, timestamp__lte=now)
        wanted = None
        if "spoof" in kinds:
            wanted = rows.filter(status=EntryLog.Status.SPOOF_SUSPECTED)
        if "repeated_unknown" in kinds:
            repeated = rows.filter(repeated_unknown=True)
            wanted = repeated if wanted is None else wanted | repeated
        found = list(wanted.order_by("timestamp")[: ALARMS_PER_ANSWER + 1])
        shown = found[:ALARMS_PER_ANSWER]
        serializer = EntryLogSerializer(shown, many=True, context={"request": request})
        for row, data in zip(shown, serializer.data, strict=True):
            data["alarm"] = "spoof" if row.status == EntryLog.Status.SPOOF_SUSPECTED else "repeated_unknown"
            answer["alarms"].append(data)
        answer["more"] = len(found) > ALARMS_PER_ANSWER
        return Response(answer)


# The same as the launcher's backup reminder (launcher.py BACKUP_REMINDER_DAYS).
BACKUP_DUE_DAYS = 7
# Warn when the data drive has less free space than this.
LOW_DISK_BYTES = 5 * 1024 ** 3


def _camera(report_row, now):
    if report_row is None:
        return None
    report = report_row.report or {}
    return {
        "online": now - report_row.updated_at <= camera_status.FRESH_FOR,
        "last_seen": report_row.updated_at,
        "camera": report.get("camera"),
        "resolution": report.get("resolution"),
        "fps": report.get("fps"),
    }


def _gates(now):
    """One row per gate heard from in the last week, from the camera reports
    every gate monitor sends about once a minute (logs/camera_status.py)."""
    reported = {}
    for row in GateCamera.objects.filter(updated_at__gte=now - camera_status.SHOW_FOR):
        reported.setdefault(lanes.gate_of(row.gate_location), {})[lanes.direction_of(row.gate_location)] = row
    today = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
    sign_in_on = gate_shifts.enabled()
    gates = []
    for gate in sorted(reported):
        rows = reported[gate]
        entrance, exit_ = rows.get(lanes.ENTRY), rows.get(lanes.EXIT)
        report = (entrance.report if entrance else None) or {}
        seen = [row.updated_at for row in rows.values()]
        last_seen = max(seen) if seen else None
        let_in = EntryLog.real.filter(gate_location=gate, timestamp__gte=today, status=EntryLog.Status.SUCCESS)
        gates.append({
            "gate": gate,
            "online": last_seen is not None and now - last_seen <= camera_status.FRESH_FOR,
            "last_seen": last_seen,
            "app_version": report.get("app_version"),
            "queue": report.get("queue"),
            "last_tap": report.get("last_tap"),
            "entrance": _camera(entrance, now),
            "exit": _camera(exit_, now),
            "on_duty": gate_shifts._on_duty(gate_shifts.active_shift(gate)) if sign_in_on else None,
            "entries_today": let_in.filter(direction=EntryLog.Direction.ENTRY).count(),
            "exits_today": let_in.filter(direction=EntryLog.Direction.EXIT).count(),
        })
    return gates, sign_in_on


def _database():
    database = settings.DATABASES["default"]
    if "sqlite" not in database["ENGINE"]:
        return {"engine": "MySQL", "size": None}
    path = str(database["NAME"])
    size = 0
    for suffix in ("", "-wal", "-shm"):
        try:
            size += Path(path + suffix).stat().st_size
        except OSError:
            pass
    return {"engine": "SQLite", "size": size}


def _disk():
    try:
        usage = shutil.disk_usage(Path(settings.MEDIA_ROOT).parent)
    except OSError:
        return None
    return {"free": usage.free, "total": usage.total}


def _last_backup(now):
    entry = (AuditLogEntry.objects.select_related("actor").filter(action=AuditLogEntry.Action.DATA_BACKED_UP)
             .order_by("-timestamp").first())
    if entry is None:
        return None
    actor = (entry.actor.get_full_name() or entry.actor.username) if entry.actor else None
    return {"at": entry.timestamp, "by": actor, "days_ago": (now - entry.timestamp).days}


def _warnings(gates, sign_in_on, backup, disk):
    warnings = []
    for gate in gates:
        name = gate["gate"]
        if not gate["online"]:
            seen = timezone.localtime(gate["last_seen"])
            when = f"{seen:%b} {seen.day}, {seen.hour % 12 or 12}:{seen:%M %p}"
            warnings.append({"level": "danger",
                             "text": f"{name}: the gate monitor isn't running - last heard from {when}."})
            continue
        for side in ("entrance", "exit"):
            camera = gate[side]
            if camera is not None and not camera["online"]:
                warnings.append({"level": "danger", "text": f"{name}: the {side} camera isn't sending."})
        if gate["queue"]:
            warnings.append({"level": "caution",
                             "text": f"{name}: {gate['queue']} card tap(s) saved while offline, waiting to be sent."})
        if sign_in_on and gate["on_duty"] is None:
            warnings.append({"level": "caution", "text": f"{name}: nobody is signed in for duty."})
    if backup is None:
        warnings.append({"level": "caution",
                         "text": "No backup has been made yet - use \"Back up data\" in the SecureTap launcher."})
    elif backup["days_ago"] > BACKUP_DUE_DAYS:
        warnings.append({"level": "caution", "text": f"The last backup was {backup['days_ago']} days ago - make a "
                                                     "new one with \"Back up data\" in the SecureTap launcher."})
    if disk is not None and disk["free"] < LOW_DISK_BYTES:
        warnings.append({"level": "danger",
                         "text": "The server's disk is nearly full - gate photos and the database need room."})
    return warnings


class SystemStatusView(APIView):
    """The System Status page. Admin and SASO only."""

    permission_classes = [IsAdminOrSaso]

    def get(self, request):
        now = timezone.now()
        gates, sign_in_on = _gates(now)
        backup = _last_backup(now)
        disk = _disk()
        return Response({
            "server_time": now,
            "gates": gates,
            "sign_in_enabled": sign_in_on,
            "server": {
                "last_backup": backup,
                "backup_due_days": BACKUP_DUE_DAYS,
                "database": _database(),
                "disk": disk,
                "enrolled_people": Person.objects.filter(is_active=True, face_embeddings__isnull=False)
                .distinct().count(),
                "people": Person.objects.filter(is_active=True).count(),
            },
            "warnings": _warnings(gates, sign_in_on, backup, disk),
        })
