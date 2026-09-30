from django.conf import settings
from rest_framework import status, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView

from audit.utils import log_action
from users import occlusion_utils
from users.confusable_utils import MAX_EMBEDDINGS_PER_CONFUSABLE_PERSON
from users.serializers import MAX_EMBEDDINGS_PER_PERSON

from .models import AdminProfile
from .permissions import IsAdmin
from .serializers import DashboardAccountSerializer, RoleTokenObtainPairSerializer


class LoginView(TokenObtainPairView):
    serializer_class = RoleTokenObtainPairSerializer


class DashboardAccountViewSet(viewsets.ModelViewSet):
    """Account Management - Admin-only (see the access matrix: neither SASO
    nor Security Officer can create/edit/deactivate other dashboard logins).
    Every create/update/deactivate is audit-logged, since "who has access to
    this system and when it changed" is exactly the kind of thing an Admin
    needs to be able to answer later."""

    queryset = AdminProfile.objects.select_related("user").all().order_by("user__username")
    serializer_class = DashboardAccountSerializer
    permission_classes = [IsAdmin]

    def perform_create(self, serializer):
        instance = serializer.save()
        log_action(
            self.request.user, "account_created",
            target_description=f"Account {instance.user.username} ({instance.role})",
        )

    def perform_update(self, serializer):
        instance = serializer.save()
        log_action(
            self.request.user, "account_updated",
            target_description=f"Account {instance.user.username} ({instance.role})",
        )

    def perform_destroy(self, instance):
        # Deactivate the login (is_active=False), not a hard delete - same
        # "soft by default" reasoning Person.perform_destroy already uses:
        # this account may be the actor on old AuditLogEntry/EntryLog rows,
        # which should keep pointing at a real (if disabled) account rather
        # than a foreign key Django would otherwise null out.
        instance.user.is_active = False
        instance.user.save(update_fields=["is_active"])
        log_action(
            self.request.user, "account_deactivated",
            target_description=f"Account {instance.user.username} ({instance.role})",
        )


class SystemSettingsView(APIView):
    """Admin-only, READ-ONLY for this pass. Surfaces the match/liveness/
    quality thresholds that actually govern the gate scan, which today live
    only in settings.py/.env, read once at process start - there is no
    runtime-editable config path yet. Deliberately scoped this way rather
    than building live editing: doing that properly needs a DB-backed config
    table plus a cache-invalidation story for already-running worker
    processes, which is a meaningfully bigger change than the rest of this
    access-control pass. Flagged as a follow-up, not silently done partway."""

    permission_classes = [IsAdmin]

    def get(self, request):
        return Response({
            "face_match_similarity_threshold": settings.FACE_MATCH_SIMILARITY_THRESHOLD,
            "confusable_similarity_threshold": settings.CONFUSABLE_SIMILARITY_THRESHOLD,
            "liveness_score_threshold": settings.LIVENESS_SCORE_THRESHOLD,
            "tiebreak_margin": settings.TIEBREAK_MARGIN,
            "tiebreak_timeout_seconds": settings.TIEBREAK_TIMEOUT_SECONDS,
            "vote_window_size": settings.VOTE_WINDOW_SIZE,
            "vote_required_agreement": settings.VOTE_REQUIRED_AGREEMENT,
            "vote_window_seconds": settings.VOTE_WINDOW_SECONDS,
            "gate_scan_det_size": settings.GATE_SCAN_DET_SIZE,
            "gate_scan_min_blur_variance": settings.GATE_SCAN_MIN_BLUR_VARIANCE,
            "face_edge_margin_ratio": settings.FACE_EDGE_MARGIN_RATIO,
            "face_max_yaw_ratio": settings.FACE_MAX_YAW_RATIO,
            "face_min_mouth_visibility_ratio": settings.FACE_MIN_MOUTH_VISIBILITY_RATIO,
            "face_max_mouth_texture_ratio": settings.FACE_MAX_MOUTH_TEXTURE_RATIO,
            "face_min_det_score_unoccluded": settings.FACE_MIN_DET_SCORE_UNOCCLUDED,
            # What's actually deciding occlusion right now - not just what's
            # configured - so a classifier that silently fell back to the
            # rules (missing model file) is visible here, not only in a log.
            "occlusion_detection_mode": occlusion_utils.active_mode_description(),
            "occlusion_classifier_threshold": settings.OCCLUSION_CLASSIFIER_THRESHOLD,
            "recognition_cooldown_seconds": settings.RECOGNITION_COOLDOWN_SECONDS,
            "unenrolled_capture_cooldown_seconds": settings.UNENROLLED_CAPTURE_COOLDOWN_SECONDS,
            "spoof_capture_cooldown_seconds": settings.SPOOF_CAPTURE_COOLDOWN_SECONDS,
            "max_embeddings_per_person": MAX_EMBEDDINGS_PER_PERSON,
            "max_embeddings_per_confusable_person": MAX_EMBEDDINGS_PER_CONFUSABLE_PERSON,
            "editable": False,
            "note": (
                "These values are read from the backend's configuration (settings.py/.env) - "
                "changing them today means editing that file and restarting the backend, not "
                "editing them here. Live editing is a planned follow-up, not yet built."
            ),
        }, status=status.HTTP_200_OK)
