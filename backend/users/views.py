import os

import pandas as pd
from django.conf import settings
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status, viewsets
from rest_framework.filters import SearchFilter
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsAdminOrIT
from .face_utils import compute_face_encoding, normalize_to_jpeg
from .models import FaceEncoding, Person
from .serializers import PersonSerializer

REQUIRED_BULK_COLUMNS = {"full_name", "role", "student_or_employee_id", "nfc_id"}
BULK_PHOTOS_SUBDIR = os.path.join("seed_data", "photos")


class PersonViewSet(viewsets.ModelViewSet):
    """User management is fully admin/it-only per spec - security can see
    people's names/photos through the logs endpoints, but not this one."""

    queryset = Person.objects.all().order_by("full_name")
    serializer_class = PersonSerializer
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ["role", "is_active", "department_or_course"]
    search_fields = ["full_name", "student_or_employee_id", "nfc_id"]
    permission_classes = [IsAdminOrIT]

    def perform_destroy(self, instance):
        # "Delete" deactivates rather than removing the row, so historical entry
        # logs referencing this person stay intact.
        instance.is_active = False
        instance.save(update_fields=["is_active"])


class BulkImportView(APIView):
    permission_classes = [IsAdminOrIT]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        upload = request.FILES.get("file")
        if upload is None:
            return Response({"detail": "No file was uploaded."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            if upload.name.lower().endswith(".csv"):
                rows = pd.read_csv(upload)
            else:
                rows = pd.read_excel(upload)
        except Exception:
            return Response(
                {"detail": "Could not parse the file. Upload a valid CSV or XLSX file."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        missing_columns = REQUIRED_BULK_COLUMNS - set(rows.columns)
        if missing_columns:
            return Response(
                {"detail": f"Missing required column(s): {', '.join(sorted(missing_columns))}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        created = 0
        errors = []
        for index, row in rows.iterrows():
            row_number = index + 2  # +1 for header row, +1 for 0-index
            try:
                created += self._import_row(row)
            except Exception as exc:
                errors.append({"row": row_number, "error": str(exc)})

        return Response({"created": created, "errors": errors}, status=status.HTTP_200_OK)

    @staticmethod
    def _import_row(row):
        full_name = str(row["full_name"]).strip()
        role = str(row["role"]).strip().lower()
        student_id = str(row["student_or_employee_id"]).strip()
        nfc_id = str(row["nfc_id"]).strip()

        if role not in (Person.Role.STUDENT, Person.Role.STAFF):
            raise ValueError(f"role must be 'student' or 'staff', got '{role}'")
        if not student_id or not nfc_id:
            raise ValueError("student_or_employee_id and nfc_id are required")
        if Person.objects.filter(student_or_employee_id=student_id).exists():
            raise ValueError(f"a person with ID '{student_id}' already exists")
        if Person.objects.filter(nfc_id=nfc_id).exists():
            raise ValueError(f"nfc_id '{nfc_id}' is already assigned to another person")

        photo_path = BulkImportView._find_photo(student_id)
        if photo_path is None:
            raise ValueError(
                f"no photo found for '{student_id}' in {BULK_PHOTOS_SUBDIR} (expected <id>.jpg/.jpeg/.png)"
            )

        with open(photo_path, "rb") as photo_fp:
            encoding = compute_face_encoding(photo_fp)
            person = Person.objects.create(
                full_name=full_name,
                role=role,
                student_or_employee_id=student_id,
                nfc_id=nfc_id,
                department_or_course=str(row.get("department_or_course", "") or "").strip(),
                photo_reference=normalize_to_jpeg(photo_fp),
            )
            FaceEncoding.objects.create(person=person, encoding=encoding.tolist())
        return 1

    @staticmethod
    def _find_photo(student_id):
        base_dir = os.path.join(settings.BASE_DIR, BULK_PHOTOS_SUBDIR)
        for ext in (".jpg", ".jpeg", ".png", ".heic", ".heif"):
            candidate = os.path.join(base_dir, f"{student_id}{ext}")
            if os.path.isfile(candidate):
                return candidate
        return None
