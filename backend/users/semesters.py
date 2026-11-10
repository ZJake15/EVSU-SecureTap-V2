"""Semester enrollment: whether a student is on the current semester's
enrolled list (the registrar's CSV) - the dashboard's Semester page.

Students re-enroll every semester, and some don't in time. A registered
student who isn't on the current list is still recognized and let in, but
the entry is flagged "Not enrolled" (EntryLog.not_enrolled, stamped in
EntryLog.save) - on the gate monitor, in Logs and on Live Monitoring - so a
guard can check them. Staff are never flagged, and until the first semester
is started nobody is.

The list is kept as normalized ID numbers (normalize_id), not links to
people, so a student registered after the list was uploaded is still found.
The gate asks for every match, so the current list is cached for a few
seconds and dropped at once when it changes (forget) - the backend is one
process (manage.py runserver).
"""

import io
import re
import threading
import time

import pandas as pd
from django.db import transaction
from django.utils import timezone

from configuration import store as system_settings

from .models import Person, Semester, SemesterEnrollment

CACHE_SECONDS = 5.0
MAX_ROWS = 100_000
PREVIEW_ROWS = 5

_cache_lock = threading.Lock()
_cache = {"at": 0.0, "key": None, "ids": None}


def normalize_id(value):
    """The form ID numbers are compared in: no spaces, upper case, and a
    spreadsheet's "2023001.0" read back as "2023001"."""
    text = re.sub(r"\s+", "", str(value if value is not None else "")).upper()
    if re.fullmatch(r"\d+\.0", text):
        text = text[:-2]
    return text[:50]


def current():
    return Semester.objects.filter(is_current=True).order_by("-started_at").first()


def forget():
    """The list changed - the next check reads it again."""
    with _cache_lock:
        _cache.update(at=0.0, key=None, ids=None)


def _current_ids():
    """The current semester's ID numbers (a set), or None with no semester started."""
    now = time.monotonic()
    with _cache_lock:
        if now - _cache["at"] < CACHE_SECONDS:
            return _cache["ids"]
    semester = current()
    key = (semester.id, semester.updated_at) if semester else None
    with _cache_lock:
        if key is not None and key == _cache["key"]:
            ids = _cache["ids"]
        elif semester is None:
            ids = None
        else:
            ids = set(semester.enrollments.values_list("student_id", flat=True))
        _cache.update(at=now, key=key, ids=ids, grace_until=grace_until(semester))
        return ids


def enabled():
    """Settings > Semester Enrollment: "Check semester enrollment"."""
    return bool(system_settings.get("semester_check_enabled"))


def refuses():
    """Settings: "A student who isn't enrolled" = Refuse entry."""
    return enabled() and system_settings.get("not_enrolled_action") == "refuse"


def shown_at_gate():
    """Whether the gate monitor shows NOT ENROLLED - always when entry is refused."""
    return refuses() or bool(system_settings.get("not_enrolled_show_at_gate"))


def grace_until(semester):
    """When the semester's grace period (Settings) ends, or None without one."""
    days = int(system_settings.get("semester_grace_days") or 0)
    return semester.started_at + timezone.timedelta(days=days) if semester and days > 0 else None


def not_enrolled(person):
    """True for a student who isn't on the current semester's list - with the
    check switched on and the semester's grace period over."""
    if person is None or person.role != Person.Role.STUDENT or not enabled():
        return False
    ids = _current_ids()
    if ids is None:
        return False
    if _cache.get("grace_until") and timezone.now() < _cache["grace_until"]:
        return False
    return normalize_id(person.student_or_employee_id) not in ids


# ---- Reading the registrar's file ------------------------------------------

def read_table(upload):
    """The uploaded list as a table of text (ID numbers keep their leading
    zeros and dashes). CSV in any common encoding and separator, or Excel.
    Raises ValueError with a message for the page."""
    data = upload.read()
    if not data:
        raise ValueError("The file is empty.")
    name = (upload.name or "").lower()
    try:
        if name.endswith((".xlsx", ".xls")):
            table = pd.read_excel(io.BytesIO(data), dtype=str)
        else:
            table = None
            for encoding in ("utf-8-sig", "cp1252", "latin-1"):
                try:
                    table = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False, encoding=encoding,
                                        sep=None, engine="python")
                    break
                except UnicodeDecodeError:
                    continue
            if table is None:
                raise ValueError
    except Exception as exc:
        raise ValueError("Couldn't read the file. Upload the CSV (or Excel) list of enrolled students.") from exc
    if len(table.columns) == 0 or len(table) == 0:
        raise ValueError("The file has no rows.")
    if len(table) > MAX_ROWS:
        raise ValueError(f"The file has {len(table):,} rows - more than the {MAX_ROWS:,} allowed.")
    table.columns = [str(column).strip() or f"Column {index + 1}" for index, column in enumerate(table.columns)]
    return table.fillna("").astype(str)


def _registered_student_ids():
    return {normalize_id(value) for value in
            Person.objects.filter(role=Person.Role.STUDENT).values_list("student_or_employee_id", flat=True)}


ID_HEADER = re.compile(r"(stud(ent)?[\s_.-]*(no|num|number|id))|(^id([\s_.-]*(no|num|number))?\.?$)|(^student$)",
                       re.IGNORECASE)


def preview(upload):
    """What the page shows before anything is saved: the columns, the first
    rows, and for each column how many of its values are ID numbers of
    students registered here - the column with the most is suggested."""
    table = read_table(upload)
    registered = _registered_student_ids()
    matches = {}
    for column in table.columns:
        values = {normalize_id(value) for value in table[column] if str(value).strip()}
        matches[column] = len(values & registered)
    suggested = max(matches, key=matches.get) if any(matches.values()) else None
    if suggested is None:
        suggested = next((column for column in table.columns if ID_HEADER.search(column)), table.columns[0])
    return {
        "columns": list(table.columns),
        "rows": table.head(PREVIEW_ROWS).to_dict(orient="records"),
        "total_rows": len(table),
        "matches": matches,
        "suggested_column": suggested,
        "registered_students": len(registered),
    }


def ids_from(upload, column):
    """The distinct ID numbers in that column of the uploaded file."""
    table = read_table(upload)
    if column not in table.columns:
        raise ValueError(f'The file has no column "{column}".')
    ids = {normalize_id(value) for value in table[column]}
    ids.discard("")
    if not ids:
        raise ValueError(f'The column "{column}" is empty.')
    return ids


# ---- Changing the list ------------------------------------------------------

def _add(semester, ids, source):
    have = set(semester.enrollments.values_list("student_id", flat=True))
    new = sorted(set(ids) - have)
    SemesterEnrollment.objects.bulk_create(
        [SemesterEnrollment(semester=semester, student_id=student_id, source=source) for student_id in new],
        batch_size=1000,
    )
    return len(new)


@transaction.atomic
def start(name, ids, user):
    """A new semester with this list - the last one stops counting."""
    Semester.objects.filter(is_current=True).update(is_current=False)
    semester = Semester.objects.create(name=name.strip()[:100], is_current=True, started_by=user)
    added = _add(semester, ids, SemesterEnrollment.Source.CSV)
    forget()
    return semester, added


@transaction.atomic
def update_list(semester, ids, replace, user=None):
    """An updated list for the current semester. replace: the uploaded list
    becomes the list (students added by hand are kept); otherwise its
    students are added to it. -> (added, removed)."""
    removed = 0
    if replace:
        removed, _ = semester.enrollments.filter(source=SemesterEnrollment.Source.CSV).exclude(
            student_id__in=ids).delete()
    added = _add(semester, ids, SemesterEnrollment.Source.CSV)
    semester.save(update_fields=["updated_at"])
    forget()
    return added, removed


@transaction.atomic
def add_by_hand(semester, ids):
    added = _add(semester, {normalize_id(value) for value in ids} - {""}, SemesterEnrollment.Source.MANUAL)
    semester.save(update_fields=["updated_at"])
    forget()
    return added


@transaction.atomic
def remove(semester, ids):
    removed, _ = semester.enrollments.filter(student_id__in={normalize_id(value) for value in ids}).delete()
    semester.save(update_fields=["updated_at"])
    forget()
    return removed


# ---- What the page shows ----------------------------------------------------

def summary(semester):
    on_list = set(semester.enrollments.values_list("student_id", flat=True))
    by_hand = semester.enrollments.filter(source=SemesterEnrollment.Source.MANUAL).count()
    students = Person.objects.filter(role=Person.Role.STUDENT, is_active=True).values_list(
        "student_or_employee_id", flat=True)
    registered = {normalize_id(value) for value in students}
    all_registered = _registered_student_ids()
    unmatched = sorted(on_list - all_registered)
    return {
        "id": semester.id,
        "name": semester.name,
        "started_at": semester.started_at,
        "started_by": (semester.started_by.get_full_name() or semester.started_by.username)
        if semester.started_by else None,
        "updated_at": semester.updated_at,
        "on_list": len(on_list),
        "added_by_hand": by_hand,
        "students_registered": len(registered),
        "registered_on_list": len(registered & on_list),
        "registered_not_on_list": len(registered - on_list),
        # On the list but not registered in SecureTap (yet) - normal for
        # students who never enrolled their face; many of them can also mean
        # the wrong column was picked.
        "list_not_registered": len(unmatched),
        "list_not_registered_sample": unmatched[:20],
        # Settings > Semester Enrollment, so the page can say what happens.
        "check_enabled": enabled(),
        "action": "refuse" if refuses() else "flag",
        "grace_until": grace_until(semester),
    }


def not_on_list(semester, search="", limit=50):
    """Registered (active) students not on this semester's list - the late enrollees to tick."""
    on_list = set(semester.enrollments.values_list("student_id", flat=True))
    people = Person.objects.filter(role=Person.Role.STUDENT, is_active=True).order_by("full_name")
    search = (search or "").strip()
    if search:
        people = people.filter(full_name__icontains=search) | people.filter(
            student_or_employee_id__icontains=search)
    missing = [person for person in people if normalize_id(person.student_or_employee_id) not in on_list]
    return {
        "total": len(missing),
        "results": [
            {"id": person.id, "full_name": person.full_name, "student_or_employee_id": person.student_or_employee_id,
             "department_or_course": person.department_or_course}
            for person in missing[:limit]
        ],
    }
