"""What each gate's camera actually does, for the Settings page's Camera group.

A camera may not have the resolution or frame rate it's asked for, and
exposure / backlight / focus are up to its driver - so instead of assuming,
every gate monitor reports what it got (entry-agent/main.py, about once a
minute and whenever it changes), and the Settings page shows it under each
Camera setting, e.g. "Main Gate: 1920x1080 · Side Gate: 1280x720 (asked
1920x1080 - this camera's nearest)".
"""

from django.utils import timezone

from .models import GateCamera

# What a report may hold, and its type - anything else is dropped.
FIELDS = {
    "camera": str, "resolution": str, "resolution_asked": str, "fps": float, "fps_asked": str,
    "scan_pause": float, "upload_size": int, "video_fps": int, "student_display_fps": int, "zoom": float,
    "exposure": str, "backlight": str, "focus": str,
}
# Gates not heard from in this long aren't shown any more.
SHOW_FOR = timezone.timedelta(days=7)
# A report older than this gets "last seen ..." - the gate monitor is probably closed.
FRESH_FOR = timezone.timedelta(minutes=3)
APPLIED = {"applied": "applied ✓", "ignored": "this camera ignored it", "camera": "camera's own"}


def save(gate_location, report):
    clean = {}
    for key, kind in FIELDS.items():
        value = report.get(key) if isinstance(report, dict) else None
        if value is None or value == "":
            continue
        try:
            clean[key] = kind(value) if kind is not str else str(value)[:100]
        except (TypeError, ValueError):
            continue
    GateCamera.objects.update_or_create(gate_location=str(gate_location)[:100], defaults={"report": clean})
    return clean


def _number(value):
    return f"{value:g}"


def _lines(report):
    """setting key -> what this gate's camera does, in a few words."""
    lines = {}
    resolution = report.get("resolution")
    asked = report.get("resolution_asked")
    if resolution:
        lines["camera_resolution"] = resolution + (
            f" (asked {asked} - this camera's nearest)" if asked and asked != "auto" and asked != resolution else ""
        )
    fps = report.get("fps")
    lines["camera_fps"] = f"about {fps:.0f} a second" if fps else "measuring…"
    if report.get("scan_pause") is not None:
        lines["scan_pause"] = f"{_number(report['scan_pause'])} s"
    if report.get("upload_size"):
        lines["upload_size"] = f"{report['upload_size']} px"
    if report.get("video_fps"):
        lines["video_fps"] = f"{report['video_fps']} a second"
    if report.get("student_display_fps"):
        lines["student_display_fps"] = f"{report['student_display_fps']} a second"
    for key, field in (("camera_exposure", "exposure"), ("backlight_compensation", "backlight"),
                       ("camera_focus", "focus")):
        if report.get(field):
            lines[key] = APPLIED.get(report[field], report[field])
    return lines


def describe(now=None):
    """Settings key -> one status line covering every gate seen lately."""
    now = now or timezone.now()
    per_key = {}
    for gate in GateCamera.objects.filter(updated_at__gte=now - SHOW_FOR).order_by("gate_location"):
        name = gate.gate_location
        if now - gate.updated_at > FRESH_FOR:
            name += f" (last seen {timezone.localtime(gate.updated_at):%b %d, %I:%M %p})"
        for key, text in _lines(gate.report).items():
            per_key.setdefault(key, []).append(f"{name}: {text}")
    return {key: " · ".join(texts) for key, texts in per_key.items()}
