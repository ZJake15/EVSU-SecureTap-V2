"""Reading and changing system settings - the one place the rest of the
backend gets a setting's current value from.

    from configuration import store
    if similarity >= store.get("match_strictness"): ...

Values are cached in memory for CACHE_SECONDS, so the gate scan (which asks
for several settings on every camera frame, ~5 times a second) doesn't hit
the database each time. A change saved from the dashboard clears this
process's cache immediately; any other backend process picks it up within
CACHE_SECONDS. Either way no restart is needed.
"""

import math
import threading
import time

from django.db import DatabaseError, transaction
from django.utils import timezone

from audit.utils import log_action

from .registry import BOOL, BY_KEY, CHOICE, DEFINITIONS, FIXED, INT, SECTIONS

CACHE_SECONDS = 5.0

_lock = threading.Lock()
_cache = {"values": None, "rows": None, "loaded_at": 0.0}


class ValidationFailed(Exception):
    """One or more submitted values broke a rule (wrong type, out of range)."""

    def __init__(self, errors):
        super().__init__(errors)
        self.errors = errors


class ConfirmationRequired(Exception):
    """A risky change (see SettingDef.risky) was submitted without the
    admin confirming it."""

    def __init__(self, items):
        super().__init__(items)
        self.items = items


# ---- reading -----------------------------------------------------------------


def _fmt(number):
    return str(int(number)) if float(number).is_integer() else f"{number:g}"


def _decode(definition, row):
    kind = definition.kind
    if kind == BOOL and row.bool_value is not None:
        return bool(row.bool_value)
    if kind == INT and row.number_value is not None:
        return int(round(row.number_value))
    if kind not in (BOOL, INT, CHOICE) and row.number_value is not None:
        return float(row.number_value)
    if kind == CHOICE and row.text_value:
        return row.text_value
    return definition.default_value()


def _encode(definition, value, row):
    row.number_value = None
    row.bool_value = None
    row.text_value = ""
    if definition.kind == BOOL:
        row.bool_value = bool(value)
    elif definition.kind == CHOICE:
        row.text_value = str(value)
    else:
        row.number_value = float(value)


def _load():
    from .models import SystemSetting

    rows = {row.key: row for row in SystemSetting.objects.select_related("changed_by")}
    missing = [d for d in DEFINITIONS if d.kind != FIXED and d.key not in rows]
    if missing:
        # First time this setting is needed: store today's value (from
        # settings.py/.env, or OFF for a new feature) so nothing changes.
        new_rows = []
        for definition in missing:
            row = SystemSetting(key=definition.key)
            _encode(definition, definition.default_value(), row)
            new_rows.append(row)
        SystemSetting.objects.bulk_create(new_rows, ignore_conflicts=True)
        rows = {row.key: row for row in SystemSetting.objects.select_related("changed_by")}

    values = {}
    for definition in DEFINITIONS:
        row = rows.get(definition.key)
        if definition.kind == FIXED or row is None:
            values[definition.key] = definition.default_value()
        else:
            values[definition.key] = _decode(definition, row)
    return values, rows


def _snapshot(force=False):
    now = time.monotonic()
    with _lock:
        if not force and _cache["values"] is not None and now - _cache["loaded_at"] < CACHE_SECONDS:
            return _cache["values"], _cache["rows"]
    try:
        values, rows = _load()
    except DatabaseError:
        # The settings table doesn't exist yet (before `manage.py migrate`) -
        # behave exactly as the system did before the Settings page.
        return {d.key: d.default_value() for d in DEFINITIONS}, {}
    with _lock:
        _cache.update(values=values, rows=rows, loaded_at=now)
    return values, rows


def get(key):
    """The current value of one setting (see registry.py for the keys)."""
    return _snapshot()[0][key]


def invalidate():
    with _lock:
        _cache["values"] = None


# ---- validating and changing ----------------------------------------------------


def validate(definition, raw):
    """(clean value, None) if `raw` is allowed for this setting, else
    (None, a plain-English reason). This is the server-side check - the
    dashboard's own limits are only a convenience on top of it."""
    kind = definition.kind
    if kind == FIXED:
        return None, "This setting can't be changed."
    if kind == BOOL:
        return (raw, None) if isinstance(raw, bool) else (None, "Must be on or off.")
    if kind == CHOICE:
        allowed = {value: label for value, label in definition.choices}
        if raw not in allowed:
            return None, "Must be one of: " + ", ".join(allowed.values()) + "."
        return raw, None

    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        return None, "Must be a number."
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return None, "Must be a number."
    if not math.isfinite(number):
        return None, "Must be a number."
    if kind == INT:
        if not number.is_integer():
            return None, "Must be a whole number."
        number = int(number)
    low, high = definition.minimum, definition.maximum
    if (low is not None and number < low) or (high is not None and number > high):
        return None, f"Must be between {_fmt(low)} and {_fmt(high)}."
    return number, None


def confirmation_reason(definition, value):
    """Why saving this value needs the admin to confirm it, or None."""
    if not definition.risky:
        return None
    if definition.kind == BOOL:
        if definition.recommended is not None and value != definition.recommended:
            return definition.off_warning or "This turns off a recommended protection."
        return None
    if definition.recommended:
        low, high = definition.recommended
        if not low <= value <= high:
            return (
                f"{_fmt(value)} is outside the recommended range of {_fmt(low)} to {_fmt(high)}. "
                "This can let the wrong people in or keep real students out."
            )
    return None


def update(changes, actor, confirmed=False):
    """Saves the submitted {key: value} changes, all or nothing, and writes
    one audit log entry per setting that actually changed. Raises
    ValidationFailed or ConfirmationRequired instead of saving anything."""
    current, _rows = _snapshot(force=True)
    errors, cleaned = {}, {}
    for key, raw in changes.items():
        definition = BY_KEY.get(key)
        if definition is None:
            errors[key] = "Unknown setting."
            continue
        value, error = validate(definition, raw)
        if error:
            errors[key] = error
        else:
            cleaned[key] = value

    merged = {**current, **cleaned}
    if (
        "frames_must_agree" not in errors and "frames_considered" not in errors
        and merged["frames_must_agree"] > merged["frames_considered"]
    ):
        errors["frames_must_agree"] = "Can't be more than the number of frames counted (Out of the last)."
    if errors:
        raise ValidationFailed(errors)

    changed = {key: value for key, value in cleaned.items() if value != current[key]}
    risky = [
        {"key": key, "label": BY_KEY[key].label, "reason": reason}
        for key, value in changed.items()
        if (reason := confirmation_reason(BY_KEY[key], value))
    ]
    if risky and not confirmed:
        raise ConfirmationRequired(risky)

    from .models import SystemSetting

    now = timezone.now()
    with transaction.atomic():
        for key, value in changed.items():
            definition = BY_KEY[key]
            row, _created = SystemSetting.objects.select_for_update().get_or_create(key=key)
            _encode(definition, value, row)
            row.changed_by = actor
            row.changed_at = now
            row.save()
            log_action(
                actor, "setting_changed",
                target_description=f"Setting: {definition.label}"[:255],
                detail={"setting": key, "label": definition.label, "old": current[key], "new": value},
            )
    invalidate()
    return changed


# ---- what the Settings page shows -----------------------------------------------------


def describe(status=None):
    """Every setting with its value, limits, wording and "last changed"
    details, grouped by section - the Settings page's whole payload.
    `status` optionally maps a key to a live note (e.g. which covered-face
    method is actually in use)."""
    values, rows = _snapshot(force=True)
    status = status or {}
    items = []
    for definition in DEFINITIONS:
        row = rows.get(definition.key)
        changed_by = None
        if row is not None and row.changed_at is not None:
            user = row.changed_by
            changed_by = (user.get_full_name() or user.username) if user else "(deleted account)"
        recommended = definition.recommended
        items.append({
            "key": definition.key,
            "section": definition.section,
            "label": definition.label,
            "description": definition.description,
            "kind": definition.kind,
            "value": values[definition.key],
            # The day-one value (settings.py/.env, or OFF for a new feature) -
            # what the page's "Reset all to defaults" button puts back.
            "default": definition.default_value(),
            "minimum": definition.minimum,
            "maximum": definition.maximum,
            "step": definition.step,
            "unit": definition.unit,
            "recommended": list(recommended) if isinstance(recommended, tuple) else recommended,
            "recommended_value": definition.recommended_value,
            "risky": definition.risky,
            "choices": [{"value": value, "label": label} for value, label in definition.choices],
            "note": definition.note,
            "off_warning": definition.off_warning,
            "depends_on": definition.depends_on,
            "requires_restart": definition.requires_restart,
            "status": status.get(definition.key),
            "changed_by": changed_by,
            "changed_at": row.changed_at.isoformat() if row is not None and row.changed_at else None,
        })
    return {
        "sections": [{"key": key, "title": title, "intro": intro} for key, title, intro in SECTIONS],
        "settings": items,
    }
