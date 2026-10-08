import numpy as np
from django.contrib.auth import authenticate
from django.core.files.base import ContentFile
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status as http_status
from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts import lockout
from accounts.models import AdminProfile
from accounts.permissions import (
    HasServiceToken,
    IsAnyDashboardRole,
    IsSecurityOfficer,
    get_assigned_gate,
    get_role,
)
from audit.utils import log_action
from securetap_project.media_auth import build_signed_media_url
from configuration import store as system_settings
from users import insightface_utils, liveness_utils, occlusion_utils
from users.confusable_utils import get_confusable_partner_ids
from users.models import Person

from . import gate_shifts
from .filters import EntryLogFilter
from .models import (
    EntryLog,
    GateShift,
    OcclusionAttempt,
    PendingTiebreak,
    RecognitionAttempt,
    SpoofAttempt,
    UnmatchedAttempt,
)
from .serializers import (
    EntryLogSerializer,
    GateSignInRequestSerializer,
    GateSignOutRequestSerializer,
    IdentifyRequestSerializer,
    ManualOverrideRequestSerializer,
    VerifyRequestSerializer,
)


def _box_to_dict(box):
    """InsightFace bounding boxes come back as (x1, y1, x2, y2) - converted
    to the {top, right, bottom, left} shape the entry-agent already expects
    (no entry-agent change needed for box drawing, just this backend's
    internal coordinate convention swapping from the old dlib layout)."""
    x1, y1, x2, y2 = box
    return {"top": y1, "right": x2, "bottom": y2, "left": x1}


def person_payload(person, request):
    """Shared profile fields returned by both /verify and /identify on a
    match, so the entry-agent can show a full profile card (photo, role, ID,
    department) instead of just a name."""
    if person is None:
        return {
            "person_name": None,
            "person_photo": None,
            "role": None,
            "student_or_employee_id": None,
            "department_or_course": None,
            "distinguishing_note": None,
        }
    photo_url = None
    if person.photo_reference:
        photo_url = build_signed_media_url(request, person.photo_reference.url)
    return {
        "person_name": person.full_name,
        "person_photo": photo_url,
        "role": person.role,
        "student_or_employee_id": person.student_or_employee_id,
        "department_or_course": person.department_or_course,
        # A manual backstop for a confusable pair (see users.models.
        # ConfusablePair) - never used by matching itself, just handed
        # through to whatever's displaying this result (dashboard, entry-
        # agent kiosk) so a guard can visually double-check it. Blank for
        # everyone else.
        "distinguishing_note": person.distinguishing_note or None,
    }


class HealthView(APIView):
    """Unauthenticated - just answers "is the backend process up and able to
    respond at all", independent of whether the caller's token/session is
    valid. The entry-agent's launcher status strip uses this to distinguish
    "server down" from "server up but my token is wrong"."""

    permission_classes = []

    def get(self, request):
        return Response({"status": "ok"})


class GateSummaryView(APIView):
    """Today's Entries/Exits/Unknown counts for the entry-agent's own local
    stats strip. This is a service-token-authenticated equivalent of the
    dashboard's /api/reports/summary (which requires a logged-in dashboard
    user, not something the entry-agent has) - deliberately lighter, just
    the three numbers the camera scanner's stats strip needs."""

    permission_classes = [HasServiceToken]

    def get(self, request):
        gate_location = request.query_params.get("gate_location")
        now = timezone.localtime()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow_start = today_start + timezone.timedelta(days=1)

        logs = EntryLog.objects.filter(timestamp__gte=today_start, timestamp__lt=tomorrow_start)
        if gate_location:
            logs = logs.filter(gate_location=gate_location)

        entries_today = logs.filter(
            direction=EntryLog.Direction.ENTRY, status=EntryLog.Status.SUCCESS
        ).count()
        exits_today = logs.filter(
            direction=EntryLog.Direction.EXIT, status=EntryLog.Status.SUCCESS
        ).count()
        unknown_today = logs.filter(status=EntryLog.Status.FAILED).count()
        spoof_today = logs.filter(status=EntryLog.Status.SPOOF_SUSPECTED).count()
        occlusion_today = logs.filter(status=EntryLog.Status.OCCLUSION_DETECTED).count()

        return Response(
            {
                "entries_today": entries_today,
                "exits_today": exits_today,
                "unknown_today": unknown_today,
                "spoof_today": spoof_today,
                "occlusion_today": occlusion_today,
                "gate_sign_in": gate_shifts.status(gate_location) if gate_location else None,
            }
        )


class GateSignInView(APIView):
    """A guard signing in at the gate monitor with their username and
    password (Settings -> "Guards sign in at the gate monitor"). A tap of
    their staff ID card does the same through VerifyView. Called by the
    entry-agent, so it authenticates the device with the service token AND
    the guard with their own password; wrong passwords count toward the
    dashboard's failed login lockout. See logs/gate_shifts.py."""

    permission_classes = [HasServiceToken]

    def post(self, request):
        serializer = GateSignInRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        gate_location = data["gate_location"]
        if not gate_shifts.enabled():
            return Response({"detail": "Gate sign-in is switched off on the Settings page."},
                            status=http_status.HTTP_400_BAD_REQUEST)

        profile = AdminProfile.objects.select_related("user").filter(user__username=data["username"]).first()
        message = lockout.locked_message(profile)
        if message:
            return Response({"detail": message}, status=http_status.HTTP_403_FORBIDDEN)
        user = authenticate(request, username=data["username"], password=data["password"])
        if user is None:
            lockout.record_failure(profile)
            return Response({"detail": "Wrong username or password."}, status=http_status.HTTP_401_UNAUTHORIZED)
        lockout.clear_failures(profile)
        refusal = gate_shifts.refusal(user, gate_location)
        if refusal:
            return Response({"detail": refusal}, status=http_status.HTTP_403_FORBIDDEN)
        gate_shifts.start_shift(user, gate_location, GateShift.Method.PASSWORD)
        return Response(gate_shifts.status(gate_location))


class GateSignOutView(APIView):
    """The guard signing out at the gate monitor - or the gate monitor
    closing/reopening, which ends whatever shift is open at that gate so
    the next entries aren't credited to someone who has left."""

    permission_classes = [HasServiceToken]

    def post(self, request):
        serializer = GateSignOutRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        gate_location = data["gate_location"]
        if data.get("shift_id"):
            shift = GateShift.objects.filter(id=data["shift_id"], gate_location=gate_location,
                                             ended_at__isnull=True).first()
            if shift is not None:
                gate_shifts.end_shift(shift, data["reason"])
        else:
            gate_shifts.end_shifts_at(gate_location, data["reason"])
        return Response(gate_shifts.status(gate_location))


class EntryLogViewSet(viewsets.ReadOnlyModelViewSet):
    """Full history/all-gates for Admin and SASO. A Security Officer gets
    neither - see get_queryset(): forced to their own assigned_gate_location
    (not whatever gate_location they might pass as a query param - the point
    of server-side scoping is that the client's request can't override it),
    and forced to today only, no arbitrary date range. There's no separate
    "no export" enforcement needed beyond that: the CSV export the dashboard
    builds is generated client-side from whatever rows this endpoint
    returned (see Logs.jsx), so an officer who can only ever fetch today's
    own-gate rows can only ever export that - the data scoping IS the export
    restriction, not a separate check. The Export button is still hidden for
    this role in the UI so it doesn't look like data is missing rather than
    deliberately withheld, but that's a UI clarity choice, not the actual
    enforcement point.

    A Security Officer with no assigned_gate_location at all (an
    unconfigured account) sees nothing rather than everything - failing
    closed, not open, on missing configuration."""

    serializer_class = EntryLogSerializer
    permission_classes = [IsAnyDashboardRole]
    filter_backends = [DjangoFilterBackend]
    filterset_class = EntryLogFilter

    def get_queryset(self):
        queryset = EntryLog.objects.select_related("person", "performed_by", "on_duty").all()
        if get_role(self.request.user) == AdminProfile.Role.SECURITY_OFFICER:
            gate = get_assigned_gate(self.request.user)
            today_start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
            queryset = queryset.filter(gate_location=gate or "__none__", timestamp__gte=today_start)
        return queryset


class LiveLogsView(APIView):
    permission_classes = [IsAnyDashboardRole]

    def get(self, request):
        since_param = request.query_params.get("since")
        since = parse_datetime(since_param) if since_param else None
        if since is None:
            since = timezone.now() - timezone.timedelta(minutes=1)

        logs = EntryLog.objects.select_related("person", "performed_by", "on_duty").filter(timestamp__gt=since)
        if get_role(request.user) == AdminProfile.Role.SECURITY_OFFICER:
            # Same server-side gate-scoping as EntryLogViewSet above - a
            # missing assigned gate fails closed (matches nothing) rather
            # than showing every gate's live feed.
            gate = get_assigned_gate(request.user)
            logs = logs.filter(gate_location=gate or "__none__")
        logs = (
            logs
            .order_by("-timestamp")[:500]
        )
        serializer = EntryLogSerializer(logs, many=True, context={"request": request})
        return Response({"results": serializer.data})


class ManualOverrideView(APIView):
    """A Security Officer's manual override: the scanner failed, they
    visually checked a physical ID, and they're logging the entry by hand.
    Distinct from VerifyView below in every way that matters for keeping
    this auditable: VerifyView authenticates the entry-agent kiosk with a
    shared service token on behalf of no particular staff member (a card tap
    isn't "someone's" decision, it's a lookup); this endpoint requires a
    real logged-in dashboard account (JWT, IsSecurityOfficer) and stamps
    EntryLog.performed_by with that specific account - so a manual override
    is never just labeled as one, it's traceably tied to who made the call.

    Always creates a fresh EntryLog row - no cooldown/dedupe merge with a
    recent automatic success the way VerifyView's NFC taps or a face match
    get, because a manual override is never "the same event" as an
    automatic one; conflating the two would hide the fact that the scanner
    needed a human to step in at all.
    """

    permission_classes = [IsSecurityOfficer]

    def post(self, request):
        serializer = ManualOverrideRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        person = Person.objects.filter(student_or_employee_id=data["student_or_employee_id"]).first()
        if person is None:
            return Response(
                {"detail": "No student/staff record matches that ID."},
                status=http_status.HTTP_404_NOT_FOUND,
            )
        if not person.is_active:
            return Response(
                {"detail": "This ID has been deactivated."}, status=http_status.HTTP_400_BAD_REQUEST,
            )

        gate = get_assigned_gate(request.user) or ""
        log = EntryLog.objects.create(
            person=person,
            direction=data["direction"],
            verification_method=EntryLog.VerificationMethod.MANUAL_OVERRIDE,
            status=EntryLog.Status.SUCCESS,
            gate_location=gate,
            failure_reason=data["reason"],  # doubles as "what was checked", shown in Logs
            performed_by=request.user,
        )
        log_action(
            request.user, "manual_override",
            target_description=f"{person.full_name} ({person.student_or_employee_id}) - {data['direction']}",
            detail={"reason": data["reason"], "gate_location": gate, "entry_log_id": log.id},
        )
        return Response(EntryLogSerializer(log, context={"request": request}).data, status=http_status.HTTP_201_CREATED)


class VerifyView(APIView):
    """Called when a card is tapped (the entry-agent's shared-secret-
    authenticated kiosk lookup - see ManualOverrideView above for the
    dashboard-side, per-account alternative when the scanner itself fails).
    A successful tap is a real gate-entry decision in its own
    right - not just a lookup - and gets logged the same way a confirmed
    face match does, appearing in Live Monitoring/Logs like any other entry.
    A rejected card (not registered / deactivated) is logged too, same as an
    unmatched face - a rejected card at the gate is a security-relevant
    event worth keeping visible.

    If the camera scan currently has an active NFC tiebreak pending for
    this gate (an ambiguous face match, awaiting a confirming tap), this tap
    resolves *that* instead - same logging, just a distinct
    verification_method (FACE_AND_CARD_TIEBREAK) so the two read differently
    in the log.

    (An earlier version of this stopped logging plain taps entirely, to fix
    routine spot-checks flooding the log with duplicate rows - but that also
    made genuine NFC-based entries invisible, which defeats the point of NFC
    as an entry method. Fixed properly below: log every tap, but dedupe
    repeated taps of the same person within RECOGNITION_COOLDOWN_SECONDS,
    the same cooldown window a face-scan entry already uses.)
    """

    permission_classes = [HasServiceToken]

    def post(self, request):
        serializer = VerifyRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        direction = data["direction"]
        gate_location = data["gate_location"]
        nfc_id = data.get("nfc_id")
        manual_id = data.get("student_or_employee_id")

        if nfc_id:
            staff = AdminProfile.objects.select_related("user").filter(staff_card_id=nfc_id).first()
            if staff is not None:
                return self._staff_card(staff, gate_location, replayed=data.get("replayed"))
            person = Person.objects.filter(nfc_id=nfc_id).first()
            not_found_reason = "Card not registered to any student or staff record."
        else:
            person = Person.objects.filter(student_or_employee_id=manual_id).first()
            not_found_reason = "ID not found."

        if person is None:
            return self._log_failure_and_respond(
                direction=direction, gate_location=gate_location,
                reason=not_found_reason, reason_code="not_registered",
            )
        if not person.is_active:
            return self._log_failure_and_respond(
                direction=direction, gate_location=gate_location,
                reason="This ID has been deactivated.", reason_code="deactivated",
            )

        verification_method = EntryLog.VerificationMethod.NFC_ONLY
        # An expired tiebreak gets its "not resolved in time" row first -
        # this used to delete it here, before IdentifyView's sweep ever saw
        # it, so a tap after the timeout silently erased that record.
        IdentifyView._expire_stale_tiebreak(gate_location)
        # Whatever is left is still live. Any successful tap resolves it,
        # whoever tapped - the tap itself, not a check against the
        # tiebreak's candidate list, is what's trusted (see documentation.md
        # §2.2 step 4).
        tiebreak = PendingTiebreak.objects.filter(gate_location=gate_location).first()
        if tiebreak is not None:
            direction = tiebreak.direction
            verification_method = (
                EntryLog.VerificationMethod.CONFUSABLE_PAIR_TIEBREAK
                if tiebreak.reason == PendingTiebreak.Reason.CONFUSABLE_PAIR
                else EntryLog.VerificationMethod.FACE_AND_CARD_TIEBREAK
            )
            tiebreak.delete()

        return self._confirm_and_respond(person, direction, gate_location, verification_method)

    def _confirm_and_respond(self, person, direction, gate_location, verification_method):
        """Dedupes a successful tap against a recent one for the same
        person - same cooldown window/idea as the face-scan side - so a
        guard tapping the same card a few times in a row (or a tiebreak
        resolution right after an ordinary tap) reuses the existing log row
        instead of creating a new one each time."""
        if verification_method == EntryLog.VerificationMethod.NFC_ONLY:
            cooldown_cutoff = timezone.now() - timezone.timedelta(seconds=system_settings.get("recognition_cooldown_seconds"))
            recent_log = (
                EntryLog.objects.filter(
                    person=person,
                    verification_method=EntryLog.VerificationMethod.NFC_ONLY,
                    status=EntryLog.Status.SUCCESS,
                    timestamp__gte=cooldown_cutoff,
                )
                .order_by("-timestamp")
                .first()
            )
            if recent_log:
                return Response(
                    {
                        "success": True,
                        "reason": None,
                        "reason_code": None,
                        "log_id": recent_log.id,
                        "deduped": True,
                        **person_payload(person, self.request),
                    },
                    status=http_status.HTTP_200_OK,
                )

        log = EntryLog.objects.create(
            person=person,
            direction=direction,
            verification_method=verification_method,
            status=EntryLog.Status.SUCCESS,
            gate_location=gate_location,
        )
        return Response(
            {
                "success": True,
                "reason": None,
                "reason_code": None,
                "log_id": log.id,
                **person_payload(person, self.request),
            },
            status=http_status.HTTP_200_OK,
        )

    def _staff_card(self, profile, gate_location, replayed=False):
        """A guard's own staff ID card: signs them in at this gate (see
        logs/gate_shifts.py) instead of being treated as a student's card.
        Never logged as a gate entry - it isn't anyone passing through."""
        def refused(reason, reason_code):
            return Response({"success": False, "staff_card": True, "reason": reason, "reason_code": reason_code,
                             **person_payload(None, self.request)})

        if replayed:
            # Tapped while the gate was offline and only sent now - signing
            # in hours later would credit the wrong guard with what happened
            # in between.
            return refused("A staff card tap saved while the gate was offline doesn't sign anyone in.",
                           "staff_replayed")
        if not gate_shifts.enabled():
            return refused("This is a staff ID card. To sign in with it, switch on \"Guards sign in at the gate "
                           "monitor\" on the Settings page.", "staff_sign_in_off")
        refusal = gate_shifts.refusal(profile.user, gate_location)
        if refusal:
            return refused(refusal, "staff_not_allowed")
        gate_shifts.start_shift(profile.user, gate_location, GateShift.Method.CARD)
        return Response({"success": True, "staff_card": True, "reason": None, "reason_code": None,
                         "gate_sign_in": gate_shifts.status(gate_location)})

    def _log_failure_and_respond(self, *, direction, gate_location, reason, reason_code):
        """A rejected card/ID is still logged - same reasoning as an
        unmatched face on the camera side, no dedup (rejections aren't the
        routine-spot-check flooding case this was originally fixed for)."""
        log = EntryLog.objects.create(
            person=None,
            direction=direction,
            verification_method=EntryLog.VerificationMethod.NFC_ONLY,
            status=EntryLog.Status.FAILED,
            gate_location=gate_location,
            failure_reason=reason,
        )
        return Response(
            {
                "success": False,
                "reason": reason,
                "reason_code": reason_code,
                "log_id": log.id,
                **person_payload(None, self.request),
            },
            status=http_status.HTTP_200_OK,
        )


class IdentifyView(APIView):
    """Called periodically by the entry-agent's continuous camera scan. There's
    no claimed identity here (unlike VerifyView) - every face detected in the
    frame is compared independently against every enrolled active person's
    embeddings (a 1:N search per face, best of up to ~5 embeddings per
    person wins), so several people walking through together in the same
    frame are each identified, not just one.

    A single frame's match is never trusted on its own: it's tallied as a
    RecognitionAttempt, and only confirmed into a real EntryLog once the
    same person has been the top match in VOTE_REQUIRED_AGREEMENT of the
    last VOTE_WINDOW_SIZE attempts for this gate. A borderline or genuinely
    ambiguous match (two close candidates) is routed to an NFC tiebreak
    instead of being auto-accepted or auto-rejected - see VerifyView for how
    that gets resolved by a card tap.

    A non-match gets the same grace period, not an immediate "Unknown" -
    tallied as an UnmatchedAttempt (grouped by embedding similarity, since
    there's no Person to key on) and only confirmed FAILED once enough
    recent attempts agree (see _confirm_or_vote_unmatched). Before either
    voting path even runs - and before the liveness check - a face too close
    to the frame edge (likely partially cut off), turned too far away from
    the camera (above FACE_MAX_YAW_RATIO), or too motion-blurred (below
    GATE_SCAN_MIN_BLUR_VARIANCE) is skipped outright - not counted as an
    attempt in either direction, just a "still checking" retry signal back to
    the entry-agent - so a single bad frame while someone's mid-stride can't
    flag them as Unknown or as a spoof attempt.

    A face whose mouth/nose read as covered (three independent signals - see
    _occlusion_reason) is intercepted the same place, right after the
    yaw/blur checks - and like those, it's never logged: it just gets a
    "please uncover your face" prompt (_occlusion_prompt) instead of the
    generic "still checking", so it's never matched (ArcFace was never given
    a fair look at the face) and never folded into "Unknown" either.

    Matching is one vectorized cosine-similarity computation over every
    enrolled embedding - fast (numpy/BLAS) for hundreds of embeddings, but
    won't scale gracefully to a huge student body without a proper vector
    index (FAISS, etc.) - deliberately not built now; not warranted at this
    project's scale.
    """

    permission_classes = [HasServiceToken]

    def post(self, request):
        serializer = IdentifyRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        direction = data["direction"]
        gate_location = data["gate_location"]
        image_file = data["image"]

        self._expire_stale_tiebreak(gate_location)

        try:
            bgr_image = insightface_utils.load_bgr_array(image_file)
            detections = insightface_utils.compute_face_embeddings_and_boxes(bgr_image)
        except Exception:
            bgr_image = None
            detections = []

        if bgr_image is None or not detections:
            # Nobody's in frame - this is the idle state for the vast majority of
            # scan cycles, not a security-relevant event, so it isn't logged.
            # Logging it would flood EntryLog with a row every ~0.2s any time the
            # gate is simply unattended, burying genuine successes/failures.
            return Response({
                "results": [self._transient_payload("No face detected.")],
                **self._agent_info(gate_location),
            })

        image_height, image_width = bgr_image.shape[:2]

        candidates = self._active_candidates()
        if not candidates:
            # A setup/config gap (nothing enrolled yet), not a per-frame security
            # event - same reasoning as above, don't log every idle cycle.
            payload = self._transient_payload("No enrolled faces to compare against.")
            return Response({
                "results": [payload],
                **self._agent_info(gate_location),
            })

        known_matrix, owners = self._build_candidate_matrix(candidates)
        results = []
        for embedding, box, det_score, blur_variance, yaw_ratio, mouth_ratio, texture_ratio in detections:
            skip_result = self._skip_reason(box, blur_variance, yaw_ratio, image_width, image_height)
            if skip_result is not None:
                results.append(skip_result)
                continue
            # Liveness (anti-spoofing) runs after quality checks but before
            # this face is ever compared against enrolled embeddings - a
            # face that looks like a photo/screen replay is never given the
            # chance to match anyone. See users/liveness_utils.py. It also
            # runs before the covered-face check below, because a covered
            # face isn't logged: a photo or screen that happens to read as
            # covered must still be caught (and logged) as a spoof.
            #
            # Settings page: "Spoof checking" off skips this entirely; an
            # "Ask-for-card range" above 0 treats a score just under the limit
            # as unsure - matched as normal, then confirmed by a card tap
            # (see _match_one_face) instead of being flagged as a fake.
            liveness_score = None
            liveness_unsure = False
            if system_settings.get("spoof_check_enabled"):
                liveness_score = liveness_utils.compute_liveness_score(bgr_image, box)
                spoof_limit = system_settings.get("spoof_strictness")
                if liveness_score < spoof_limit:
                    unsure_range = system_settings.get("spoof_unsure_range")
                    if unsure_range > 0 and liveness_score >= spoof_limit - unsure_range:
                        liveness_unsure = True
                    else:
                        results.append(
                            self._confirm_or_vote_spoof(
                                embedding, bgr_image, box, direction, gate_location, request, liveness_score
                            )
                        )
                        continue
            # Checked before matching - an occluded face is never given a
            # fair ArcFace comparison (it would just produce a distorted,
            # unusable embedding). Never logged, but it carries its own
            # "please uncover your face" prompt instead of a generic
            # "Checking..." - see _occlusion_prompt.
            #
            # Skipped outright for a face that already matches an enrolled
            # person - see _already_recognizable - and when "Covered face
            # detection" is switched off on the Settings page.
            if (
                system_settings.get("covered_face_enabled")
                and not self._already_recognizable(embedding, known_matrix)
                and self._occlusion_reason(mouth_ratio, texture_ratio, det_score)
            ):
                results.append(self._occlusion_prompt(box, gate_location))
                continue
            results.append(
                self._match_one_face(
                    embedding, box, owners, known_matrix, direction, gate_location, request, bgr_image,
                    liveness_score, liveness_unsure=liveness_unsure,
                )
            )
        return Response({
            "results": results,
            "image_size": {"width": image_width, "height": image_height},
            **self._agent_info(gate_location),
        })

    @staticmethod
    def _agent_info(gate_location):
        """Sent with every /api/identify answer, so the gate monitor always
        shows - and acts on - the current Settings-page values without a
        restart: the match strictness it displays, whether to sound the
        alarm for a suspected fake, and whether guards sign in here and
        who's on duty (so a sign-out elsewhere, or a shift reaching its time
        limit, shows up within a frame or two)."""
        return {
            "threshold": system_settings.get("match_strictness"),
            "liveness_threshold": system_settings.get("spoof_strictness"),
            "alerts": {"spoof": system_settings.get("alert_spoof")},
            "gate_sign_in": gate_shifts.status(gate_location),
        }

    @staticmethod
    def _active_candidates():
        return [
            person
            for person in Person.objects.filter(is_active=True).prefetch_related("face_embeddings")
            if person.face_embeddings.all()  # already prefetched, no extra query
        ]

    @staticmethod
    def _build_candidate_matrix(candidates):
        """Stacks every embedding of every candidate person into one
        (N, 512) matrix plus a parallel owner list, so one vectorized
        similarity computation covers all of them - a person with several
        embeddings just occupies several rows, and their score is the max
        across their own rows (best-matching photo wins)."""
        rows = []
        owners = []
        for person in candidates:
            for face_embedding in person.face_embeddings.all():
                rows.append(face_embedding.embedding)
                owners.append(person)
        return np.array(rows, dtype=np.float32), owners

    @staticmethod
    def _skip_reason(box, blur_variance, yaw_ratio, image_width, image_height):
        """None of these counts as a real attempt at all - a face partially
        out of frame, turned away from the camera, or too blurry produces an
        unreliable embedding, so it's skipped rather than matched (and
        definitely rather than logged as "no match"). Returns a transient,
        non-logged payload with retry=True for the entry-agent to show as
        "still checking" instead of "Unknown", or None if the face is usable.

        Ordered as "is the whole face here, is it facing me, is it sharp" -
        the checks a person could actually act on come first, so the hint
        shown over their box is the most useful one when several apply.
        """
        if system_settings.get("whole_face_required") and insightface_utils.box_touches_edge(
            box, image_width, image_height, system_settings.get("edge_margin")
        ):
            return {
                "success": False,
                "retry": True,
                "reason": "Face partially out of frame - move fully into view.",
                "hint": "Move into view",
                "log_id": None,
                "box": _box_to_dict(box),
            }
        # Runs before BOTH the liveness check and any matching, which is the
        # whole point: a turned face is the one input that can fail in either
        # direction. ArcFace embeddings are trained on roughly frontal faces,
        # so a profile view of an enrolled person matches nobody and is voted
        # through as Unknown; the same angle can also drag the liveness score
        # under its threshold and log them as a spoof attempt. Skipping means
        # the scan just waits for the frame where they look at the camera.
        # yaw_ratio is None when the detector gave no usable keypoints - fall
        # through rather than reject on a measurement that never happened.
        if yaw_ratio is not None and yaw_ratio > system_settings.get("max_turn"):
            return {
                "success": False,
                "retry": True,
                "reason": "Face turned away - look toward the camera.",
                "hint": "Face the camera",
                "log_id": None,
                "box": _box_to_dict(box),
            }
        if blur_variance < system_settings.get("blur_min"):
            return {
                "success": False,
                "retry": True,
                "reason": "Image too blurry - hold steady for a clearer frame.",
                "hint": "Hold steady",
                "log_id": None,
                "box": _box_to_dict(box),
            }
        return None

    @staticmethod
    def _occlusion_reason(mouth_ratio, texture_ratio, det_score):
        """Whether this frame's mouth reads as covered - checks THREE
        independent signals and flags occlusion if ANY fires, because each
        one misses different real cases (verified per-signal on the same
        test set, not assumed - see each signal's own docstring/setting
        comment for the numbers):

        - mouth_ratio (insightface_utils.mouth_visibility_ratio): mouth width
          over inter-eye distance, from the detector's 5 keypoints. Real but
          thin separation; badly under-detects partial coverage on its own.
        - texture_ratio (insightface_utils.lower_face_texture_ratio): how
          texture-rich the lower face looks vs. the upper face, from the
          bounding box - doesn't trust the keypoints at all, so it catches
          partial-coverage cases mouth_ratio misses.
        - det_score: the detector's own per-face detection confidence ("how
          face-like is this", not a match score) - fails independently of
          both ratios above, since a covered face can still regress a
          plausible-looking keypoint arrangement (see mouth_visibility_
          ratio's docstring for why keypoint *plausibility* specifically was
          tested and does NOT discriminate occlusion for this detector) while
          still looking less face-like to the detector overall.

        A plain bool, not a skip-style payload, because unlike
        _skip_reason's checks this doesn't fall through silently - a True
        here routes to _occlusion_prompt instead, which builds the actual
        response. Any signal being unusable (ratios None from missing
        keypoints/box, or the frame never got this far because it already
        failed an earlier check) just drops that signal - never flag on a
        measurement that never happened. det_score always exists once a face
        is detected at all, so it has no None case.

        The three signals above describe the default "rules" mode. With
        OCCLUSION_DETECTION_MODE=classifier, the same three measurements go
        to the trained Random Forest instead, which weighs them together
        rather than checking each against its own cutoff - see
        users/occlusion_utils.py, including when it falls back to the rules
        on its own."""
        return occlusion_utils.is_occluded(mouth_ratio, texture_ratio, det_score)

    @staticmethod
    def _already_recognizable(embedding, known_matrix):
        """True if this face already matches some enrolled person at the
        normal match threshold - in which case it's never flagged as covered.

        The covered-face check exists so a person can be recognized; a face
        that already is recognizable doesn't need to uncover anything, and a
        genuinely covered face can't produce a confident match in the first
        place. This matters because the check misfires on uncovered faces:
        measured on labeled photos, it flags a large share of UNCOVERED faces
        as covered when the face is small or soft in the frame (far from the
        camera), and a flagged frame is never matched - so without this, an
        enrolled person could be told to uncover a face that isn't covered,
        and never be recognized.

        Only a similarity check (a single matrix product against the same
        known_matrix _match_one_face uses) - no voting, no logging. A face
        that clears it still goes through liveness and the normal match vote
        afterwards, including the near-threshold tiebreak (card tap), so a
        weak match here can't wave anyone through on its own."""
        query = np.array(embedding, dtype=np.float32)
        return float((known_matrix @ query).max()) >= system_settings.get("match_strictness")

    def _match_one_face(
        self, embedding, box, owners, known_matrix, direction, gate_location, request, bgr_image, liveness_score,
        liveness_unsure=False,
    ):
        query = np.array(embedding, dtype=np.float32)
        similarities = known_matrix @ query  # both sides are unit-normalized -> cosine similarity
        best_index = int(np.argmax(similarities))
        best_similarity = float(similarities[best_index])
        best_person = owners[best_index]

        if best_similarity < system_settings.get("match_strictness"):
            return self._confirm_or_vote_unmatched(
                embedding, bgr_image, box, direction, gate_location, request, best_similarity, liveness_score
            )

        # Checked before the normal ambiguity-gap logic below, and overrides
        # it outright: a confusable pair (see users.models.ConfusablePair) is
        # a known, standing fact about this specific person, not a property
        # of this one score gap - it forces a card tap regardless of how
        # clean best_similarity looks, which is exactly the case identical
        # twins can slip past a borderline/close_second check (each twin can
        # score confidently as themselves without the gap ever looking
        # small). No 2D face system can be expected to tell them apart
        # visually, so this doesn't try to - it routes to the one check that
        # doesn't share that weakness.
        # The fake-face check was unsure about this face (Settings page,
        # "Ask-for-card range"): it matched someone, so their card settles it.
        if liveness_unsure:
            return self._start_tiebreak(
                gate_location, direction, [best_person.id], box, best_similarity,
                reason=PendingTiebreak.Reason.LIVENESS_UNSURE,
            )

        # Settings page: "Always ask look-alikes for a card".
        confusable_partner_ids = (
            get_confusable_partner_ids(best_person.id) if system_settings.get("lookalikes_always_tap") else []
        )
        if confusable_partner_ids:
            candidate_ids = [best_person.id] + confusable_partner_ids
            return self._start_tiebreak(
                gate_location, direction, candidate_ids, box, best_similarity,
                reason=PendingTiebreak.Reason.CONFUSABLE_PAIR,
            )

        other_best = None
        other_owner = None
        for sim, owner in zip(similarities, owners):
            if owner.id == best_person.id:
                continue
            if other_best is None or sim > other_best:
                other_best, other_owner = float(sim), owner

        borderline = best_similarity < system_settings.get("match_strictness") + system_settings.get("unsure_margin")
        close_second = other_best is not None and (best_similarity - other_best) < system_settings.get("unsure_margin")
        # Settings page: "Ask for card when unsure".
        if (borderline or close_second) and system_settings.get("ask_card_when_unsure"):
            candidate_ids = [best_person.id] + ([other_owner.id] if close_second else [])
            return self._start_tiebreak(gate_location, direction, candidate_ids, box, best_similarity)

        return self._confirm_or_vote(
            best_person, best_similarity, direction, gate_location, request, box, liveness_score
        )

    def _confirm_or_vote(self, person, similarity, direction, gate_location, request, box, liveness_score):
        """A face match is never confirmed from a single frame - it has to
        be the top match in enough recent scans first (see class docstring).
        Not-yet-confirmed matches come back with log_id: null, which the
        entry-agent's existing dedup-aware UI already treats as "not a new
        event yet" with no entry-agent changes needed."""
        RecognitionAttempt.objects.create(gate_location=gate_location, person=person, similarity=similarity)

        window_cutoff = timezone.now() - timezone.timedelta(seconds=system_settings.get("decision_window_seconds"))
        recent_attempts = list(
            RecognitionAttempt.objects.filter(gate_location=gate_location, timestamp__gte=window_cutoff)
            .order_by("-timestamp")[: system_settings.get("frames_considered")]
        )
        agreement = sum(1 for attempt in recent_attempts if attempt.person_id == person.id)

        if agreement < system_settings.get("frames_must_agree"):
            return {
                "success": True,
                "retry": False,
                "reason": None,
                "log_id": None,
                "similarity": similarity,
                "liveness_score": liveness_score,
                "box": _box_to_dict(box),
                **person_payload(person, request),
            }

        cooldown_cutoff = timezone.now() - timezone.timedelta(seconds=system_settings.get("recognition_cooldown_seconds"))
        recent_log = (
            EntryLog.objects.filter(
                person=person,
                verification_method=EntryLog.VerificationMethod.FACE_ONLY,
                status=EntryLog.Status.SUCCESS,
                timestamp__gte=cooldown_cutoff,
            )
            .order_by("-timestamp")
            .first()
        )
        if recent_log:
            return {
                "success": True,
                "retry": False,
                "reason": None,
                "log_id": recent_log.id,
                "similarity": similarity,
                "liveness_score": liveness_score,
                "deduped": True,
                "box": _box_to_dict(box),
                **person_payload(person, request),
            }

        # A fresh log row (not a cooldown-reuse) is the one point worth
        # checking whether this encounter involved an occluded frame a
        # moment ago - "matched fine, but their face was briefly covered
        # just before this" is worth keeping on the record even though the
        # scan ultimately succeeded (see EntryLog.occlusion_detected).
        occlusion_seen = self._recent_occlusion_seen(gate_location)
        log = EntryLog.objects.create(
            person=person,
            direction=direction,
            verification_method=EntryLog.VerificationMethod.FACE_ONLY,
            status=EntryLog.Status.SUCCESS,
            gate_location=gate_location,
            match_confidence=similarity,
            liveness_score=liveness_score,
            occlusion_detected=occlusion_seen,
        )
        return {
            "success": True,
            "retry": False,
            "reason": None,
            "log_id": log.id,
            "similarity": similarity,
            "liveness_score": liveness_score,
            "occlusion_detected": occlusion_seen,
            "box": _box_to_dict(box),
            **person_payload(person, request),
        }

    @staticmethod
    def _start_tiebreak(
        gate_location, direction, candidate_ids, box, best_similarity,
        reason=PendingTiebreak.Reason.AMBIGUOUS_MATCH,
    ):
        PendingTiebreak.objects.update_or_create(
            gate_location=gate_location,
            defaults={"candidate_person_ids": candidate_ids, "direction": direction, "reason": reason},
        )
        candidates = list(Person.objects.filter(id__in=candidate_ids))
        candidate_names = [candidate.full_name for candidate in candidates]
        is_confusable = reason == PendingTiebreak.Reason.CONFUSABLE_PAIR
        # Handed through so the entry-agent can show the guard a visual
        # backstop to check by eye while waiting for the tap - never used to
        # decide the match itself (see users.models.Person.distinguishing_note).
        distinguishing_notes = [
            candidate.distinguishing_note for candidate in candidates if candidate.distinguishing_note
        ]
        return {
            "success": False,
            "retry": False,
            "tiebreak_required": True,
            "confusable_pair": is_confusable,
            "reason": (
                "This match is confirmed but flagged as easily confused with someone similar - "
                "please tap your card to confirm."
                if is_confusable
                else "The fake-face check was unsure - please tap your card to confirm."
                if reason == PendingTiebreak.Reason.LIVENESS_UNSURE
                else "Ambiguous match - please tap your card to confirm."
            ),
            "log_id": None,
            "similarity": best_similarity,
            "box": _box_to_dict(box),
            "candidate_names": candidate_names,
            "distinguishing_notes": distinguishing_notes,
        }

    @staticmethod
    def _expire_stale_tiebreak(gate_location):
        """No Celery/cron in this project - the continuous scan's own
        polling is the heartbeat that notices a tiebreak nobody resolved in
        time and logs it as unresolved instead of leaving it in limbo."""
        cutoff = timezone.now() - timezone.timedelta(seconds=system_settings.get("card_wait_seconds"))
        stale = PendingTiebreak.objects.filter(gate_location=gate_location, created_at__lt=cutoff).first()
        if stale is None:
            return
        is_confusable = stale.reason == PendingTiebreak.Reason.CONFUSABLE_PAIR
        if is_confusable:
            # No card tap arrived in time, and this was never an ambiguous
            # score to begin with - the match itself looked fine. Still not
            # auto-accepted: person stays None (attributing it to whichever
            # candidate scored highest would be exactly the false confidence
            # this feature exists to prevent), and the candidate names go
            # into failure_reason so whoever reviews this FAILED row has
            # something to act on, rather than a bare "unresolved".
            candidate_names = list(
                Person.objects.filter(id__in=stale.candidate_person_ids).values_list("full_name", flat=True)
            )
            failure_reason = (
                "Confusable-pair match not confirmed by card tap in time - possibly one of: "
                f"{', '.join(candidate_names)}. Needs manual guard/staff review."
            )
        elif stale.reason == PendingTiebreak.Reason.LIVENESS_UNSURE:
            failure_reason = "Fake-face check unsure, not confirmed by card tap in time."
        else:
            failure_reason = "Ambiguous match, not resolved by card tap in time."
        EntryLog.objects.create(
            person=None,
            direction=stale.direction,
            verification_method=(
                EntryLog.VerificationMethod.CONFUSABLE_PAIR_TIEBREAK
                if is_confusable
                else EntryLog.VerificationMethod.FACE_ONLY
            ),
            status=EntryLog.Status.FAILED,
            gate_location=gate_location,
            failure_reason=failure_reason,
        )
        stale.delete()

    def _confirm_or_vote_unmatched(
        self, embedding, bgr_image, box, direction, gate_location, request, best_similarity, liveness_score,
    ):
        """Symmetric with _confirm_or_vote: an unrecognized face is never
        logged as "Unknown" from a single frame either. There's no Person to
        key voting on the way a match has, so recent UnmatchedAttempt rows
        for this gate are grouped by embedding similarity instead - close
        enough counts as "probably the same unrecognized face across
        frames". Only once enough recent attempts agree (same
        VOTE_REQUIRED_AGREEMENT/VOTE_WINDOW_SIZE/VOTE_WINDOW_SECONDS a match
        uses) does this hand off to _handle_unmatched_face to actually write
        the FAILED log - previously that happened on the very first
        unmatched frame, which is what let a single blurry frame flag
        someone as Unknown.

        Checked against real production data (not assumed): a continuous
        hand-over-face covering gesture produces a MIX of frames - some
        cross the occlusion thresholds (see _occlusion_reason), some don't,
        because a hand shifts slightly frame to frame. Before this check,
        those two outcomes voted in two fully independent, unrelated
        buckets (UnmatchedAttempt here, OcclusionAttempt over in
        _occlusion_prompt) - both accumulating from the SAME
        physical event, racing each other, and "Unknown" won the race just
        as often as occlusion did. The result, seen directly in real
        EntryLog rows: occlusion_detected and failed/Unknown alternating
        every few seconds for what was clearly one uninterrupted attempt to
        cover a face.

        So: while covered frames outnumber unrecognized ones at this gate
        in the last few seconds (_occlusion_dominates), the Unknown vote
        is held back and the person stays on the "please uncover your
        face" prompt - it's far more likely one covering gesture than a
        newly-arrived stranger. A majority, not "any covered frame at
        all": covered faces aren't logged, so if one stray covered read
        were enough, an uncovered stranger whose face the covered-face
        check sometimes misreads (it does, for small/soft faces - see
        documentation §10) could keep the Unknown vote held off and never
        reach the log. Every non-match is still recorded below, so once
        the covered frames stop dominating, the vote already has them."""
        UnmatchedAttempt.objects.create(gate_location=gate_location, embedding=embedding)

        if self._occlusion_dominates(gate_location):
            # occlusion_suspected (not just retry+hint) so the entry-agent's
            # box stays a consistent teal "please uncover your face" for the
            # whole gesture, rather than flickering between this and the
            # neutral gray "Checking..." a plain retry would otherwise show -
            # a guard watching the screen should see one stable signal for
            # one continuous event, not a flicker between two.
            return {
                "success": False,
                "retry": True,
                "occlusion_suspected": True,
                "reason": "Checking - possible occlusion.",
                "hint": "Please uncover your face",
                "log_id": None,
                "similarity": best_similarity,
                "liveness_score": liveness_score,
                "box": _box_to_dict(box),
            }

        query = np.array(embedding, dtype=np.float32)
        window_cutoff = timezone.now() - timezone.timedelta(seconds=system_settings.get("decision_window_seconds"))
        recent_attempts = list(
            UnmatchedAttempt.objects.filter(gate_location=gate_location, timestamp__gte=window_cutoff)
            .order_by("-timestamp")[: system_settings.get("frames_considered")]
        )
        agreement = sum(
            1
            for attempt in recent_attempts
            if float(np.dot(np.array(attempt.embedding, dtype=np.float32), query))
            >= system_settings.get("match_strictness")
        )

        if agreement < system_settings.get("frames_must_agree"):
            return {
                "success": False,
                "retry": True,
                "reason": "Checking...",
                "log_id": None,
                "similarity": best_similarity,
                "liveness_score": liveness_score,
                "box": _box_to_dict(box),
            }

        return self._handle_unmatched_face(
            embedding, bgr_image, box, direction, gate_location, request, best_similarity, liveness_score
        )

    def _handle_unmatched_face(
        self, embedding, bgr_image, box, direction, gate_location, request, best_similarity, liveness_score,
    ):
        """Only reached once _confirm_or_vote_unmatched has enough recent
        agreement to trust this as a real non-match, not a blurry fluke. An
        unrecognized face has no Person to key a cooldown on the way a
        matched face does, so instead this compares the new embedding
        against every unmatched face logged in the last
        UNENROLLED_CAPTURE_COOLDOWN_SECONDS: close enough counts as "the same
        stranger still standing there" and reuses that log row rather than
        creating a new one and capturing another photo."""
        reason = "Not enrolled - no matching student/staff record."
        cooldown_cutoff = timezone.now() - timezone.timedelta(seconds=system_settings.get("unknown_cooldown_seconds"))
        recent_unmatched = EntryLog.objects.filter(
            verification_method=EntryLog.VerificationMethod.FACE_ONLY,
            status=EntryLog.Status.FAILED,
            timestamp__gte=cooldown_cutoff,
        ).exclude(unmatched_encoding__isnull=True)

        query = np.array(embedding, dtype=np.float32)
        for log in recent_unmatched:
            stored = np.array(log.unmatched_encoding, dtype=np.float32)
            similarity = float(np.dot(stored, query))
            if similarity >= system_settings.get("match_strictness"):
                payload = {
                    "success": False,
                    "retry": False,
                    "reason": reason,
                    "person_name": None,
                    "log_id": log.id,
                    "similarity": best_similarity,
                    "deduped": True,
                    "box": _box_to_dict(box),
                }
                if log.captured_photo:
                    payload["captured_photo"] = build_signed_media_url(request, log.captured_photo.url)
                return payload

        captured_photo_bytes = insightface_utils.crop_face(bgr_image, box)
        # Same reasoning as _confirm_or_vote's success path: a face that
        # ultimately failed to match anyone might still have been briefly
        # covered a moment earlier in this encounter, and that's worth
        # keeping on the record too, not just when the outcome is a success.
        occlusion_seen = self._recent_occlusion_seen(gate_location)
        payload = self._failure_payload(
            direction, gate_location, reason, best_similarity, captured_photo_bytes, request, embedding, box,
            liveness_score, occlusion_seen,
        )
        # Settings page: "Alert on repeated unknown faces" - flag it for the
        # gate monitor when this same face has now been logged as Unknown at
        # this gate enough times within the set period.
        if system_settings.get("alert_repeated_unknown"):
            sightings = self._unknown_sightings(gate_location, embedding)
            if sightings >= system_settings.get("repeated_unknown_count"):
                payload["repeated_unknown"] = True
                payload["repeated_unknown_count"] = sightings
        return payload

    @staticmethod
    def _unknown_sightings(gate_location, embedding):
        """How many Unknown rows at this gate within the "Within" period
        (Settings page) are this same face - this one included."""
        cutoff = timezone.now() - timezone.timedelta(minutes=system_settings.get("repeated_unknown_minutes"))
        recent = EntryLog.objects.filter(
            gate_location=gate_location,
            person__isnull=True,
            status=EntryLog.Status.FAILED,
            verification_method=EntryLog.VerificationMethod.FACE_ONLY,
            timestamp__gte=cutoff,
        ).exclude(unmatched_encoding__isnull=True).values_list("unmatched_encoding", flat=True)
        query = np.array(embedding, dtype=np.float32)
        limit = system_settings.get("match_strictness")
        return sum(
            1 for stored in recent if float(np.dot(np.array(stored, dtype=np.float32), query)) >= limit
        )

    @staticmethod
    def _failure_payload(
        direction, gate_location, reason, similarity=None, captured_photo_bytes=None, request=None,
        embedding=None, box=None, liveness_score=None, occlusion_seen=False,
    ):
        log = EntryLog(
            person=None,
            direction=direction,
            verification_method=EntryLog.VerificationMethod.FACE_ONLY,
            status=EntryLog.Status.FAILED,
            gate_location=gate_location,
            failure_reason=reason,
            liveness_score=liveness_score,
            occlusion_detected=occlusion_seen,
        )
        if embedding is not None:
            log.unmatched_encoding = list(embedding)
        if captured_photo_bytes is not None:
            log.captured_photo.save("unenrolled_capture.jpg", ContentFile(captured_photo_bytes), save=False)
        log.save()

        payload = {
            "success": False, "retry": False, "reason": reason, "person_name": None, "log_id": log.id,
            "occlusion_detected": occlusion_seen,
        }
        if similarity is not None:
            payload["similarity"] = similarity
        if liveness_score is not None:
            payload["liveness_score"] = liveness_score
        if box is not None:
            payload["box"] = _box_to_dict(box)
        if log.captured_photo and request is not None:
            payload["captured_photo"] = build_signed_media_url(request, log.captured_photo.url)
        return payload

    @staticmethod
    def _recent_occlusion_seen(gate_location):
        """Whether an occluded frame was seen at this gate recently enough
        to still count as "the same encounter" - used to decide
        whether a face match or non-match that follows should carry the
        occlusion_detected note (see EntryLog.occlusion_detected). Reuses
        VOTE_WINDOW_SECONDS rather than inventing a separate "recent"
        setting - that's already this system's definition of one continuous
        interaction at a gate."""
        window_cutoff = timezone.now() - timezone.timedelta(seconds=system_settings.get("decision_window_seconds"))
        return OcclusionAttempt.objects.filter(
            gate_location=gate_location, timestamp__gte=window_cutoff
        ).exists()

    @staticmethod
    def _occlusion_dominates(gate_location):
        """Whether covered frames outnumber unrecognized ones at this gate
        within VOTE_WINDOW_SECONDS - the hold-back condition for the
        Unknown vote (see _confirm_or_vote_unmatched)."""
        window_cutoff = timezone.now() - timezone.timedelta(seconds=system_settings.get("decision_window_seconds"))
        covered = OcclusionAttempt.objects.filter(gate_location=gate_location, timestamp__gte=window_cutoff).count()
        if not covered:
            return False
        unmatched = UnmatchedAttempt.objects.filter(gate_location=gate_location, timestamp__gte=window_cutoff).count()
        return covered > unmatched

    def _occlusion_prompt(self, box, gate_location):
        """A covered face is never logged - no EntryLog row, no captured
        photo - it only gets the on-screen "Please uncover your face" prompt
        until the person uncovers and is matched (or not) normally. Covering
        your face isn't inherently adversarial (a scarf, a cough, a phone
        call), so it isn't treated as an event worth a log entry.

        An OcclusionAttempt is still recorded: _occlusion_dominates reads
        these to hold back an "Unknown" that flickers in between covered
        frames, and _recent_occlusion_seen to note occlusion_detected on
        the entry that follows
        (see EntryLog.occlusion_detected). They're working state for that,
        not a log - the dashboard never shows them."""
        OcclusionAttempt.objects.create(gate_location=gate_location)
        return {
            "success": False,
            # retry: not a decided outcome, so the entry-agent shows the
            # prompt on the face's box but never adds it to its live log or
            # stats (those only take results carrying a log_id).
            "retry": True,
            # occlusion_suspected: this frame's own outcome is occlusion -
            # same role spoof_suspected plays for a suspected spoof, and
            # what the entry-agent checks to show its own box label/status
            # (distinct from occlusion_detected, which any outcome can
            # carry as a note - see EntryLog.occlusion_detected).
            "occlusion_suspected": True,
            "occlusion_detected": True,
            "reason": "Face partially covered - please uncover your mouth/nose and rescan.",
            "hint": "Please uncover your face",
            "log_id": None,
            "box": _box_to_dict(box),
        }

    def _confirm_or_vote_spoof(self, embedding, bgr_image, box, direction, gate_location, request, liveness_score):
        """A failed liveness check is never logged as spoof_suspected from a
        single frame either - same grace-period reasoning _confirm_or_vote
        and _confirm_or_vote_unmatched already use (see class docstring), so
        unusual lighting or a motion-blurred real frame can't alone flag
        someone as a spoof attempt. There's no confirmed identity to key
        voting on (liveness is checked before matching - see post()), so
        recent SpoofAttempt rows for this gate are grouped by embedding
        similarity instead, the same way _confirm_or_vote_unmatched groups
        UnmatchedAttempt rows - reusing the exact same VOTE_WINDOW_SIZE/
        VOTE_REQUIRED_AGREEMENT/VOTE_WINDOW_SECONDS settings a face match/
        non-match already votes with."""
        SpoofAttempt.objects.create(gate_location=gate_location, embedding=embedding, liveness_score=liveness_score)

        query = np.array(embedding, dtype=np.float32)
        window_cutoff = timezone.now() - timezone.timedelta(seconds=system_settings.get("decision_window_seconds"))
        recent_attempts = list(
            SpoofAttempt.objects.filter(gate_location=gate_location, timestamp__gte=window_cutoff)
            .order_by("-timestamp")[: system_settings.get("frames_considered")]
        )
        agreement = sum(
            1
            for attempt in recent_attempts
            if attempt.liveness_score < system_settings.get("spoof_strictness")
            and float(np.dot(np.array(attempt.embedding, dtype=np.float32), query))
            >= system_settings.get("match_strictness")
        )

        if agreement < system_settings.get("frames_must_agree"):
            # Unlike an unmatched face (which stays neutral "Checking..."
            # until confirmed, so a real person can't be flagged Unknown off
            # one bad frame), a suspected spoof is flagged red on the entry-
            # agent's preview as soon as THIS frame's liveness score misses
            # the threshold - retry=True still keeps it out of the log/
            # stats/alarm until the vote actually confirms it, but a guard
            # should see the warning the moment it's suspected, not several
            # frames later.
            return {
                "success": False,
                "retry": True,
                "spoof_suspected": True,
                "reason": "Checking...",
                "log_id": None,
                "liveness_score": liveness_score,
                "box": _box_to_dict(box),
            }

        return self._handle_spoof_face(embedding, bgr_image, box, direction, gate_location, request, liveness_score)

    def _handle_spoof_face(self, embedding, bgr_image, box, direction, gate_location, request, liveness_score):
        """Only reached once _confirm_or_vote_spoof has enough recent
        agreement to trust this as a real spoof attempt, not a one-off
        misread. A spoof attempt has no Person to key a cooldown on the way
        a matched face does, so instead this compares the new embedding
        against every spoof-suspected face logged in the last
        SPOOF_CAPTURE_COOLDOWN_SECONDS: close enough counts as "the same
        photo/screen still being held up" and reuses that log row rather
        than creating a new one and capturing another photo - same pattern
        as _handle_unmatched_face's stranger-still-there dedup."""
        reason = "Possible spoof detected - photo, screen, or printout, not a live face."
        cooldown_cutoff = timezone.now() - timezone.timedelta(seconds=system_settings.get("spoof_cooldown_seconds"))
        recent_spoof = EntryLog.objects.filter(
            status=EntryLog.Status.SPOOF_SUSPECTED, timestamp__gte=cooldown_cutoff
        ).exclude(unmatched_encoding__isnull=True)

        query = np.array(embedding, dtype=np.float32)
        for log in recent_spoof:
            stored = np.array(log.unmatched_encoding, dtype=np.float32)
            similarity = float(np.dot(stored, query))
            if similarity >= system_settings.get("match_strictness"):
                payload = {
                    "success": False,
                    "retry": False,
                    "spoof_suspected": True,
                    "reason": reason,
                    "person_name": None,
                    "log_id": log.id,
                    "liveness_score": liveness_score,
                    "deduped": True,
                    "box": _box_to_dict(box),
                }
                if log.captured_photo:
                    payload["captured_photo"] = build_signed_media_url(request, log.captured_photo.url)
                return payload

        captured_photo_bytes = insightface_utils.crop_face(bgr_image, box)
        # Same reasoning as the success/failed paths - occlusion moments
        # before this spoof suspicion was confirmed is still worth a note
        # (e.g. a hand came down holding up the photo/screen that then
        # failed liveness).
        occlusion_seen = self._recent_occlusion_seen(gate_location)
        log = EntryLog(
            person=None,
            direction=direction,
            verification_method=EntryLog.VerificationMethod.FACE_ONLY,
            status=EntryLog.Status.SPOOF_SUSPECTED,
            gate_location=gate_location,
            failure_reason=reason,
            liveness_score=liveness_score,
            unmatched_encoding=list(embedding),
            occlusion_detected=occlusion_seen,
        )
        log.captured_photo.save("spoof_capture.jpg", ContentFile(captured_photo_bytes), save=False)
        log.save()

        payload = {
            "success": False,
            "retry": False,
            "spoof_suspected": True,
            "reason": reason,
            "person_name": None,
            "log_id": log.id,
            "liveness_score": liveness_score,
            "occlusion_detected": occlusion_seen,
            "box": _box_to_dict(box),
        }
        if log.captured_photo:
            payload["captured_photo"] = build_signed_media_url(request, log.captured_photo.url)
        return payload

    @staticmethod
    def _transient_payload(reason):
        """Same shape as _failure_payload but doesn't write an EntryLog row -
        for idle states with no face to actually evaluate against anyone."""
        return {"success": False, "reason": reason, "person_name": None, "log_id": None}
