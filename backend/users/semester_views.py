"""The dashboard's Semester page (Admin and SASO): start a semester with the
registrar's enrolled-students list, update it, and tick late enrollees by
hand - see users/semesters.py. Every change is written to the Audit Log."""

from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsAdminOrSaso
from audit.models import AuditLogEntry
from audit.utils import log_action

from . import semesters
from .models import Semester

MAX_HAND_IDS = 500


def _error(message):
    return Response({"detail": message}, status=status.HTTP_400_BAD_REQUEST)


def _current_or_error():
    semester = semesters.current()
    return semester, (None if semester else _error("No semester has been started yet."))


class SemesterView(APIView):
    """The current semester (and its counts) and the past ones."""

    permission_classes = [IsAdminOrSaso]

    def get(self, request):
        semester = semesters.current()
        past = [
            {"id": item.id, "name": item.name, "started_at": item.started_at, "on_list": item.enrollments.count()}
            for item in Semester.objects.filter(is_current=False).order_by("-started_at")[:10]
        ]
        return Response({"current": semesters.summary(semester) if semester else None, "past": past})


class SemesterPreviewView(APIView):
    """An uploaded list read, not saved: its columns, first rows, and which
    column looks like the student ID number."""

    permission_classes = [IsAdminOrSaso]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        upload = request.FILES.get("file")
        if upload is None:
            return _error("Choose the list's file.")
        try:
            return Response(semesters.preview(upload))
        except ValueError as exc:
            return _error(str(exc))


class SemesterStartView(APIView):
    """A new semester with this list - the last one stops counting."""

    permission_classes = [IsAdminOrSaso]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        name = str(request.data.get("name") or "").strip()
        upload = request.FILES.get("file")
        if not name:
            return _error("Name the semester, e.g. \"1st Semester 2026-2027\".")
        if upload is None:
            return _error("Choose the list's file.")
        try:
            ids = semesters.ids_from(upload, str(request.data.get("column") or ""))
        except ValueError as exc:
            return _error(str(exc))
        semester, added = semesters.start(name, ids, request.user)
        log_action(request.user, AuditLogEntry.Action.SEMESTER_STARTED, target_description=semester.name[:255],
                   detail={"students_on_list": added, "file": upload.name, "column": request.data.get("column")})
        return Response(semesters.summary(semester), status=status.HTTP_201_CREATED)


class SemesterUploadView(APIView):
    """An updated list for the current semester: added to it, or replacing it."""

    permission_classes = [IsAdminOrSaso]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        semester, error = _current_or_error()
        if error:
            return error
        upload = request.FILES.get("file")
        if upload is None:
            return _error("Choose the list's file.")
        replace = str(request.data.get("mode") or "add") == "replace"
        try:
            ids = semesters.ids_from(upload, str(request.data.get("column") or ""))
        except ValueError as exc:
            return _error(str(exc))
        added, removed = semesters.update_list(semester, ids, replace)
        log_action(request.user, AuditLogEntry.Action.SEMESTER_LIST_UPDATED, target_description=semester.name[:255],
                   detail={"how": "replaced" if replace else "added", "file": upload.name, "added": added,
                           "removed": removed})
        return Response({"added": added, "removed": removed, **semesters.summary(semester)})


class SemesterNotOnListView(APIView):
    """Registered students not on the current list - the late enrollees to tick."""

    permission_classes = [IsAdminOrSaso]

    def get(self, request):
        semester, error = _current_or_error()
        if error:
            return error
        return Response(semesters.not_on_list(semester, request.query_params.get("search", "")))


class SemesterHandView(APIView):
    """Adds (or takes off) ID numbers by hand: {"student_ids": [...], "remove": false}."""

    permission_classes = [IsAdminOrSaso]
    parser_classes = [JSONParser]

    def post(self, request):
        semester, error = _current_or_error()
        if error:
            return error
        ids = request.data.get("student_ids")
        if not isinstance(ids, list) or not ids or len(ids) > MAX_HAND_IDS:
            return _error(f"Give 1 to {MAX_HAND_IDS} ID numbers.")
        ids = [str(value) for value in ids]
        if request.data.get("remove"):
            changed = semesters.remove(semester, ids)
            how = "removed by hand"
        else:
            changed = semesters.add_by_hand(semester, ids)
            how = "added by hand"
        log_action(request.user, AuditLogEntry.Action.SEMESTER_LIST_UPDATED, target_description=semester.name[:255],
                   detail={"how": how, "student_ids": ids[:50], "changed": changed})
        return Response({"changed": changed, **semesters.summary(semester)})
