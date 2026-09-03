import os

import pandas as pd
from django.conf import settings
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import AdminProfile
from accounts.permissions import IsAdmin, IsAdminOrSaso, get_role
from audit.utils import log_action

from .confusable_utils import find_confusable_candidates, record_confusable_pairs
from .insightface_utils import compute_face_embedding, ensure_supported_image_format, normalize_to_jpeg
from .models import ConfusablePair, DeactivationRequest, FaceEmbedding, Person
from .serializers import (
    ConfusablePairSerializer,
    DeactivationRequestSerializer,
    PersonSerializer,
    max_embeddings_for,
)

REQUIRED_BULK_COLUMNS = {"full_name", "role", "student_or_employee_id", "nfc_id"}
BULK_PHOTOS_SUBDIR = os.path.join("seed_data", "photos")


def _falsey(value):
    """request.data["is_active"] arrives as a real bool on a JSON body but as
    a string on multipart/form-data (the main edit form uses the latter) -
    normalizes both so the maker-checker check below can't be bypassed just
    by which content-type a particular request happened to use."""
    return value is False or str(value).strip().lower() in ("false", "0")


class PersonViewSet(viewsets.ModelViewSet):
    """User Management - full access for Admin and SASO (enroll/edit/bulk-
    import), per the access matrix. The one thing SASO doesn't get is the
    power to deactivate a record on their own: both destroy() and update()
    below intercept a SASO's attempt to set someone inactive and turn it into
    a DeactivationRequest awaiting Admin approval instead of applying it -
    see DeactivationRequestViewSet for the approve/reject side.
    delete_permanently is Admin-only (see get_permissions), since it's
    irreversible and well beyond "deactivate" in the first place."""

    queryset = Person.objects.all().order_by("full_name")
    serializer_class = PersonSerializer
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ["role", "is_active", "department_or_course"]
    search_fields = ["full_name", "student_or_employee_id", "nfc_id"]
    permission_classes = [IsAdminOrSaso]

    def get_permissions(self):
        if self.action == "delete_permanently":
            return [IsAdmin()]
        return super().get_permissions()

    def _create_deactivation_request(self, request, person):
        reason = str(request.data.get("reason") or "").strip()
        if not reason:
            return Response(
                {"reason": "A reason is required to request deactivation."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        existing = DeactivationRequest.objects.filter(
            person=person, status=DeactivationRequest.Status.PENDING
        ).first()
        if existing is not None:
            return Response(
                {"detail": "A deactivation request for this person is already pending Admin approval."},
                status=status.HTTP_409_CONFLICT,
            )
        deactivation_request = DeactivationRequest.objects.create(
            person=person, requested_by=request.user, reason=reason,
        )
        log_action(
            request.user, "deactivation_requested",
            target_description=f"{person.full_name} ({person.student_or_employee_id})",
            detail={"reason": reason, "request_id": deactivation_request.id},
        )
        return Response(
            {
                "detail": "Deactivation request submitted - awaiting Admin approval.",
                "request_id": deactivation_request.id,
            },
            status=status.HTTP_202_ACCEPTED,
        )

    def update(self, request, *args, **kwargs):
        # A PATCH/PUT setting is_active=False is functionally the same act as
        # DELETE (the dashboard's own "Reactivate" button already reuses this
        # same endpoint the other direction) - so it has to go through the
        # identical maker-checker gate for a SASO, not just the DELETE route.
        # Reactivating (is_active=True) is left alone: the brief only
        # restricts the deactivating direction, and undoing a deactivation
        # isn't the irreversible/security-sensitive action maker-checker
        # exists for.
        if get_role(request.user) == AdminProfile.Role.SASO and _falsey(request.data.get("is_active")):
            return self._create_deactivation_request(request, self.get_object())
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if get_role(request.user) == AdminProfile.Role.SASO:
            return self._create_deactivation_request(request, instance)
        # Admin: immediate, same as before this feature existed - an Admin
        # deactivating IS the approval, there's no separate checker above
        # them in this system.
        self.perform_destroy(instance)
        log_action(
            request.user, "deactivation_approved",
            target_description=f"{instance.full_name} ({instance.student_or_employee_id})",
            detail={"direct": True},
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

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
        to be, instead of being deleted or orphaned. Admin-only (see
        get_permissions) - this is well beyond what maker-checker even
        covers, since it destroys history, not just access."""
        person = self.get_object()
        description = f"{person.full_name} ({person.student_or_employee_id})"
        person.delete()
        log_action(request.user, "person_deleted_permanently", target_description=description)
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

        # This action takes the file straight off request.FILES rather than
        # through PersonSerializer, so it needs the format check explicitly -
        # the serializer's validate_photo never runs on this path.
        try:
            ensure_supported_image_format(photo)
        except ValueError as exc:
            return Response({"photo": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        existing_count = person.face_embeddings.count()
        cap = max_embeddings_for(person)
        if existing_count >= cap:
            return Response(
                {"photo": f"Already has the maximum of {cap} enrollment photos."},
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
        # Same check as PersonSerializer.create()/update() - a confusable
        # match can just as easily surface on the 3rd or 5th guided-capture
        # photo as the 1st, and the cap above already widens the moment this
        # detects one, so a person mid-guided-capture can keep going past 5.
        matches = find_confusable_candidates(embedding, exclude_person_id=person.id)
        confusable_partners = record_confusable_pairs(person, matches) if matches else []
        return Response(
            {
                "embedding_count": existing_count + 1,
                "max_embeddings": max_embeddings_for(person),
                "confusable_partners": confusable_partners,
            },
            status=status.HTTP_201_CREATED,
        )


class PhotoQualityCheckView(APIView):
    """Stateless pre-check for the guided multi-shot enrollment UI: runs the
    same checks enrollment does - accepted format, then the quality checks
    compute_face_embedding() applies (blur, face count, face size) - but
    doesn't save anything, since there's no Person to attach a photo to yet
    while the registrar is still filling in capture slots. Lets the UI show
    pass/fail per slot immediately instead of only finding out at final
    submit, so a rejected format is caught on the slot rather than after all
    five shots are done."""

    permission_classes = [IsAdminOrSaso]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        photo = request.FILES.get("photo")
        if photo is None:
            return Response({"ok": False, "reason": "A photo file is required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            ensure_supported_image_format(photo)
            _embedding, det_score = compute_face_embedding(photo)
        except ValueError as exc:
            return Response({"ok": False, "reason": str(exc)})
        return Response({"ok": True, "detection_score": det_score})


class BulkImportView(APIView):
    permission_classes = [IsAdminOrSaso]
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
        confusable_warnings = []
        for index, row in rows.iterrows():
            row_number = index + 2  # +1 for header row, +1 for 0-index
            try:
                person, warnings = self._import_row(row)
                created += 1
                # Bulk import has no live operator to prompt mid-capture the
                # way guided enrollment does (see PersonSerializer.create's
                # docstring context) - a flagged row still gets recorded and
                # reported here, but any "capture extra photos" follow-up has
                # to happen afterward, through the normal add_photo flow.
                for warning in warnings:
                    # warning's own "full_name"/"student_or_employee_id" refer
                    # to the confusable PARTNER, not the row just imported -
                    # named distinctly here so spreading it can't silently
                    # clobber this row's own identity with the partner's.
                    confusable_warnings.append(
                        {
                            "row": row_number,
                            "imported_full_name": person.full_name,
                            "imported_student_or_employee_id": person.student_or_employee_id,
                            **warning,
                        }
                    )
            except Exception as exc:
                errors.append({"row": row_number, "error": str(exc)})

        return Response(
            {"created": created, "errors": errors, "confusable_warnings": confusable_warnings},
            status=status.HTTP_200_OK,
        )

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
            matches = find_confusable_candidates(embedding, exclude_person_id=person.id)
            warnings = record_confusable_pairs(person, matches) if matches else []
        return person, warnings

    @staticmethod
    def _find_photo(student_id):
        base_dir = os.path.join(settings.BASE_DIR, BULK_PHOTOS_SUBDIR)
        for ext in (".jpg", ".jpeg", ".png", ".heic", ".heif"):
            candidate = os.path.join(base_dir, f"{student_id}{ext}")
            if os.path.isfile(candidate):
                return candidate
        return None


class DeactivationRequestViewSet(viewsets.ReadOnlyModelViewSet):
    """The maker-checker queue: a SASO's attempt to deactivate a Person (via
    PersonViewSet.destroy()/update()) lands here as a pending row instead of
    taking effect immediately. Admin sees every request; a SASO sees only
    the ones they themselves filed (so they can check on status), matching
    the same "own actions only" scoping the Audit Log gives them - see
    audit/views.py. Only an Admin can resolve one, via the approve/reject
    actions below - a SASO filing a request can't also be the one who
    approves it, which is the entire point of maker-checker."""

    queryset = DeactivationRequest.objects.select_related(
        "person", "requested_by", "resolved_by"
    ).all()
    serializer_class = DeactivationRequestSerializer
    permission_classes = [IsAdminOrSaso]

    def get_queryset(self):
        queryset = super().get_queryset()
        if get_role(self.request.user) == AdminProfile.Role.SASO:
            queryset = queryset.filter(requested_by=self.request.user)
        return queryset

    @action(detail=True, methods=["post"], url_path="approve", permission_classes=[IsAdmin])
    def approve(self, request, pk=None):
        deactivation_request = self.get_object()
        if deactivation_request.status != DeactivationRequest.Status.PENDING:
            return Response(
                {"detail": "This request has already been resolved."}, status=status.HTTP_400_BAD_REQUEST,
            )
        deactivation_request.person.is_active = False
        deactivation_request.person.save(update_fields=["is_active"])
        deactivation_request.status = DeactivationRequest.Status.APPROVED
        deactivation_request.resolved_by = request.user
        deactivation_request.resolved_at = timezone.now()
        deactivation_request.resolution_note = str(request.data.get("note") or "").strip()
        deactivation_request.save()
        log_action(
            request.user, "deactivation_approved",
            target_description=(
                f"{deactivation_request.person.full_name} "
                f"({deactivation_request.person.student_or_employee_id})"
            ),
            detail={"request_id": deactivation_request.id, "requested_by": deactivation_request.requested_by_id},
        )
        return Response(DeactivationRequestSerializer(deactivation_request).data)

    @action(detail=True, methods=["post"], url_path="reject", permission_classes=[IsAdmin])
    def reject(self, request, pk=None):
        deactivation_request = self.get_object()
        if deactivation_request.status != DeactivationRequest.Status.PENDING:
            return Response(
                {"detail": "This request has already been resolved."}, status=status.HTTP_400_BAD_REQUEST,
            )
        deactivation_request.status = DeactivationRequest.Status.REJECTED
        deactivation_request.resolved_by = request.user
        deactivation_request.resolved_at = timezone.now()
        deactivation_request.resolution_note = str(request.data.get("note") or "").strip()
        deactivation_request.save()
        log_action(
            request.user, "deactivation_rejected",
            target_description=(
                f"{deactivation_request.person.full_name} "
                f"({deactivation_request.person.student_or_employee_id})"
            ),
            detail={"request_id": deactivation_request.id, "requested_by": deactivation_request.requested_by_id},
        )
        return Response(DeactivationRequestSerializer(deactivation_request).data)


class ConfusablePairViewSet(
    mixins.ListModelMixin, mixins.CreateModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet
):
    """List/manually-flag/unflag confusable pairs. Admin and SASO both get
    full access here, same as PersonViewSet - SASO already has full enroll/
    edit power over Person records and is the more likely of the two to
    actually notice a real-world mix-up first (a guard reports it to them,
    not to an Admin directly). No update() - a pair either exists or it
    doesn't; there's nothing on it worth editing in place, only removing (if
    it was flagged in error) and re-creating.

    Unflagging is a deliberate, real capability here (not left out): a false
    positive at CONFUSABLE_SIMILARITY_THRESHOLD is plausible for two people
    who simply share common facial structure without being a genuine
    lookalike risk, and permanently forcing a card tap for two people who
    don't need it is its own real cost to the people involved - unlike
    DeactivationRequest, this isn't a maker-checker action (removing a pair
    doesn't lock anyone out of anything), so no approval step is needed."""

    queryset = ConfusablePair.objects.select_related("person_a", "person_b", "flagged_by").all()
    serializer_class = ConfusablePairSerializer
    permission_classes = [IsAdminOrSaso]

    def perform_create(self, serializer):
        pair = serializer.save()
        if not getattr(pair, "was_newly_created", True):
            return  # already flagged from before - nothing actually changed
        log_action(
            self.request.user, "confusable_pair_flagged",
            target_description=f"{pair.person_a.full_name} <-> {pair.person_b.full_name}",
            detail={"source": pair.source},
        )

    def perform_destroy(self, instance):
        log_action(
            self.request.user, "confusable_pair_removed",
            target_description=f"{instance.person_a.full_name} <-> {instance.person_b.full_name}",
            detail={"source": instance.source},
        )
        instance.delete()
