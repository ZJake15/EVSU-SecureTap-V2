import os

import pandas as pd
from django.conf import settings
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsAdminOrIT
from .insightface_utils import compute_face_embedding, normalize_to_jpeg
from .models import FaceEmbedding, Person
from .serializers import MAX_EMBEDDINGS_PER_PERSON, PersonSerializer

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
        # The default DELETE deactivates rather than removing the row, so
        # historical entry logs referencing this person stay intact. Actual
        # removal is the separate "permanent" action below - deliberately
        # not the same endpoint, so the ordinary Deactivate button can never
        # accidentally become irreversible.
        instance.is_active = False
        instance.save(update_fields=["is_active"])

    @action(detail=True, methods=["delete"], url_path="permanent")
    def delete_permanently(self, request, pk=None):
        """Actually removes the Person row (not a soft-deactivate). Safe to
        do at the database level - EntryLog.person is SET_NULL, so past
        entry/exit logs survive and just show "Unknown" where the name used
        to be, instead of being deleted or orphaned."""
        person = self.get_object()
        person.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"], url_path="photos")
    def add_photo(self, request, pk=None):
        """Appends one more enrollment photo/embedding to an existing
        person - the guided multi-photo capture flow calls this repeatedly
        (up to MAX_EMBEDDINGS_PER_PERSON) after the person's already been
        created with their first photo via the normal create() call."""
        person = self.get_object()
        photo = request.FILES.get("photo")
        if photo is None:
            return Response({"photo": "A photo file is required."}, status=status.HTTP_400_BAD_REQUEST)

        existing_count = person.face_embeddings.count()
        if existing_count >= MAX_EMBEDDINGS_PER_PERSON:
            return Response(
                {"photo": f"Already has the maximum of {MAX_EMBEDDINGS_PER_PERSON} enrollment photos."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            embedding, det_score = compute_face_embedding(photo)
        except ValueError as exc:
            return Response({"photo": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        photo.seek(0)
        FaceEmbedding.objects.create(
            person=person, embedding=embedding, source_image=photo, detection_score=det_score,
        )
        return Response(
            {"embedding_count": existing_count + 1, "max_embeddings": MAX_EMBEDDINGS_PER_PERSON},
            status=status.HTTP_201_CREATED,
        )


class PhotoQualityCheckView(APIView):
    """Stateless pre-check for the guided multi-shot enrollment UI: runs the
    same quality checks compute_face_embedding() uses at enrollment time
    (blur, face count, face size), but doesn't save anything - there's no
    Person to attach a photo to yet while the registrar is still filling in
    capture slots. Lets the UI show pass/fail per slot immediately instead
    of only finding out at final submit."""

    permission_classes = [IsAdminOrIT]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        photo = request.FILES.get("photo")
        if photo is None:
            return Response({"ok": False, "reason": "A photo file is required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            _embedding, det_score = compute_face_embedding(photo)
        except ValueError as exc:
            return Response({"ok": False, "reason": str(exc)})
        return Response({"ok": True, "detection_score": det_score})


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
            embedding, det_score = compute_face_embedding(photo_fp)
            photo_fp.seek(0)
            person = Person.objects.create(
                full_name=full_name,
                role=role,
                student_or_employee_id=student_id,
                nfc_id=nfc_id,
                department_or_course=str(row.get("department_or_course", "") or "").strip(),
                photo_reference=normalize_to_jpeg(photo_fp),
            )
            photo_fp.seek(0)
            # Bulk import only ever has the one photo the spreadsheet points
            # at, with no live guided-capture quality gate a human confirmed
            # in the moment - flagged so the dashboard/thesis evaluation can
            # tell these apart from guided enrollments.
            FaceEmbedding.objects.create(
                person=person, embedding=embedding, source_image=photo_fp,
                detection_score=det_score, is_low_confidence=True,
            )
        return 1

    @staticmethod
    def _find_photo(student_id):
        base_dir = os.path.join(settings.BASE_DIR, BULK_PHOTOS_SUBDIR)
        for ext in (".jpg", ".jpeg", ".png", ".heic", ".heif"):
            candidate = os.path.join(base_dir, f"{student_id}{ext}")
            if os.path.isfile(candidate):
                return candidate
        return None
