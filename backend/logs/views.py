import numpy as np
from django.conf import settings
from django.core.files.base import ContentFile
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status as http_status
from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import HasServiceToken, IsSecurityOrAbove
from users import insightface_utils, liveness_utils
from users.models import Person

from .filters import EntryLogFilter
from .models import EntryLog, PendingTiebreak, RecognitionAttempt, SpoofAttempt, UnmatchedAttempt
from .serializers import EntryLogSerializer, IdentifyRequestSerializer, VerifyRequestSerializer


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
        }
    photo_url = None
    if person.photo_reference:
        photo_url = request.build_absolute_uri(person.photo_reference.url)
    return {
        "person_name": person.full_name,
        "person_photo": photo_url,
        "role": person.role,
        "student_or_employee_id": person.student_or_employee_id,
        "department_or_course": person.department_or_course,
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

        return Response(
            {
                "entries_today": entries_today,
                "exits_today": exits_today,
                "unknown_today": unknown_today,
                "spoof_today": spoof_today,
            }
        )


class EntryLogViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = EntryLog.objects.select_related("person").all()
    serializer_class = EntryLogSerializer
    permission_classes = [IsSecurityOrAbove]
    filter_backends = [DjangoFilterBackend]
    filterset_class = EntryLogFilter


class LiveLogsView(APIView):
    permission_classes = [IsSecurityOrAbove]

    def get(self, request):
        since_param = request.query_params.get("since")
        since = parse_datetime(since_param) if since_param else None
        if since is None:
            since = timezone.now() - timezone.timedelta(minutes=1)

        logs = (
            EntryLog.objects.select_related("person")
            .filter(timestamp__gt=since)
            .order_by("-timestamp")[:500]
        )
        serializer = EntryLogSerializer(logs, many=True, context={"request": request})
        return Response({"results": serializer.data})


class VerifyView(APIView):
    """Called when a card is tapped, or when the guard uses the manual
    ID-entry fallback (same endpoint, either nfc_id or student_or_employee_id
    is provided). A successful tap is a real gate-entry decision in its own
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
        tiebreak = PendingTiebreak.objects.filter(gate_location=gate_location).first()
        if tiebreak is not None:
            cutoff = timezone.now() - timezone.timedelta(seconds=settings.TIEBREAK_TIMEOUT_SECONDS)
            still_active = tiebreak.created_at >= cutoff
            tiebreak_direction = tiebreak.direction
            tiebreak.delete()
            if still_active:
                direction = tiebreak_direction
                verification_method = EntryLog.VerificationMethod.FACE_AND_CARD_TIEBREAK
            # Expired - IdentifyView's own sweep already logs the unresolved
            # attempt on its next call; this tap is treated as a plain
            # successful NFC entry below, not a tiebreak resolution.

        return self._confirm_and_respond(person, direction, gate_location, verification_method)

    def _confirm_and_respond(self, person, direction, gate_location, verification_method):
        """Dedupes a successful tap against a recent one for the same
        person - same cooldown window/idea as the face-scan side - so a
        guard tapping the same card a few times in a row (or a tiebreak
        resolution right after an ordinary tap) reuses the existing log row
        instead of creating a new one each time."""
        if verification_method == EntryLog.VerificationMethod.NFC_ONLY:
            cooldown_cutoff = timezone.now() - timezone.timedelta(seconds=settings.RECOGNITION_COOLDOWN_SECONDS)
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
    voting path even runs, a face too close to the frame edge (likely
    partially cut off) or too motion-blurred (below GATE_SCAN_MIN_BLUR_VARIANCE)
    is skipped outright - not counted as an attempt in either direction, just
    a "still checking" retry signal back to the entry-agent - so a single bad
    frame while someone's mid-stride can't flag them as Unknown.

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
                "threshold": settings.FACE_MATCH_SIMILARITY_THRESHOLD,
                "liveness_threshold": settings.LIVENESS_SCORE_THRESHOLD,
            })

        image_height, image_width = bgr_image.shape[:2]

        candidates = self._active_candidates()
        if not candidates:
            # A setup/config gap (nothing enrolled yet), not a per-frame security
            # event - same reasoning as above, don't log every idle cycle.
            payload = self._transient_payload("No enrolled faces to compare against.")
            return Response({
                "results": [payload],
                "threshold": settings.FACE_MATCH_SIMILARITY_THRESHOLD,
                "liveness_threshold": settings.LIVENESS_SCORE_THRESHOLD,
            })

        known_matrix, owners = self._build_candidate_matrix(candidates)
        results = []
        for embedding, box, det_score, blur_variance in detections:
            skip_result = self._skip_reason(box, blur_variance, image_width, image_height)
            if skip_result is not None:
                results.append(skip_result)
                continue
            # Liveness (anti-spoofing) runs after quality checks but before
            # this face is ever compared against enrolled embeddings - a
            # face that looks like a photo/screen replay is never given the
            # chance to match anyone. See users/liveness_utils.py.
            liveness_score = liveness_utils.compute_liveness_score(bgr_image, box)
            if liveness_score < settings.LIVENESS_SCORE_THRESHOLD:
                results.append(
                    self._confirm_or_vote_spoof(
                        embedding, bgr_image, box, direction, gate_location, request, liveness_score
                    )
                )
                continue
            results.append(
                self._match_one_face(
                    embedding, box, owners, known_matrix, direction, gate_location, request, bgr_image,
                    liveness_score,
                )
            )
        return Response({
            "results": results,
            "image_size": {"width": image_width, "height": image_height},
            "threshold": settings.FACE_MATCH_SIMILARITY_THRESHOLD,
            "liveness_threshold": settings.LIVENESS_SCORE_THRESHOLD,
        })

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
    def _skip_reason(box, blur_variance, image_width, image_height):
        """Neither of these counts as a real attempt at all - a face this
        close to the frame edge or this blurry produces an unreliable
        embedding, so it's skipped rather than matched (and definitely
        rather than logged as "no match"). Returns a transient, non-logged
        payload with retry=True for the entry-agent to show as "still
        checking" instead of "Unknown", or None if the face is usable."""
        if insightface_utils.box_touches_edge(box, image_width, image_height, settings.FACE_EDGE_MARGIN_RATIO):
            return {
                "success": False,
                "retry": True,
                "reason": "Face partially out of frame - move fully into view.",
                "log_id": None,
                "box": _box_to_dict(box),
            }
        if blur_variance < settings.GATE_SCAN_MIN_BLUR_VARIANCE:
            return {
                "success": False,
                "retry": True,
                "reason": "Image too blurry - hold steady for a clearer frame.",
                "log_id": None,
                "box": _box_to_dict(box),
            }
        return None

    def _match_one_face(
        self, embedding, box, owners, known_matrix, direction, gate_location, request, bgr_image, liveness_score,
    ):
        query = np.array(embedding, dtype=np.float32)
        similarities = known_matrix @ query  # both sides are unit-normalized -> cosine similarity
        best_index = int(np.argmax(similarities))
        best_similarity = float(similarities[best_index])
        best_person = owners[best_index]

        if best_similarity < settings.FACE_MATCH_SIMILARITY_THRESHOLD:
            return self._confirm_or_vote_unmatched(
                embedding, bgr_image, box, direction, gate_location, request, best_similarity, liveness_score
            )

        other_best = None
        other_owner = None
        for sim, owner in zip(similarities, owners):
            if owner.id == best_person.id:
                continue
            if other_best is None or sim > other_best:
                other_best, other_owner = float(sim), owner

        borderline = best_similarity < settings.FACE_MATCH_SIMILARITY_THRESHOLD + settings.TIEBREAK_MARGIN
        close_second = other_best is not None and (best_similarity - other_best) < settings.TIEBREAK_MARGIN
        if borderline or close_second:
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

        window_cutoff = timezone.now() - timezone.timedelta(seconds=settings.VOTE_WINDOW_SECONDS)
        recent_attempts = list(
            RecognitionAttempt.objects.filter(gate_location=gate_location, timestamp__gte=window_cutoff)
            .order_by("-timestamp")[: settings.VOTE_WINDOW_SIZE]
        )
        agreement = sum(1 for attempt in recent_attempts if attempt.person_id == person.id)

        if agreement < settings.VOTE_REQUIRED_AGREEMENT:
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

        cooldown_cutoff = timezone.now() - timezone.timedelta(seconds=settings.RECOGNITION_COOLDOWN_SECONDS)
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

        log = EntryLog.objects.create(
            person=person,
            direction=direction,
            verification_method=EntryLog.VerificationMethod.FACE_ONLY,
            status=EntryLog.Status.SUCCESS,
            gate_location=gate_location,
            match_confidence=similarity,
            liveness_score=liveness_score,
        )
        return {
            "success": True,
            "retry": False,
            "reason": None,
            "log_id": log.id,
            "similarity": similarity,
            "liveness_score": liveness_score,
            "box": _box_to_dict(box),
            **person_payload(person, request),
        }

    @staticmethod
    def _start_tiebreak(gate_location, direction, candidate_ids, box, best_similarity):
        PendingTiebreak.objects.update_or_create(
            gate_location=gate_location,
            defaults={"candidate_person_ids": candidate_ids, "direction": direction},
        )
        candidate_names = list(Person.objects.filter(id__in=candidate_ids).values_list("full_name", flat=True))
        return {
            "success": False,
            "retry": False,
            "tiebreak_required": True,
            "reason": "Ambiguous match - please tap your card to confirm.",
            "log_id": None,
            "similarity": best_similarity,
            "box": _box_to_dict(box),
            "candidate_names": candidate_names,
        }

    @staticmethod
    def _expire_stale_tiebreak(gate_location):
        """No Celery/cron in this project - the continuous scan's own
        polling is the heartbeat that notices a tiebreak nobody resolved in
        time and logs it as unresolved instead of leaving it in limbo."""
        cutoff = timezone.now() - timezone.timedelta(seconds=settings.TIEBREAK_TIMEOUT_SECONDS)
        stale = PendingTiebreak.objects.filter(gate_location=gate_location, created_at__lt=cutoff).first()
        if stale is None:
            return
        EntryLog.objects.create(
            person=None,
            direction=stale.direction,
            verification_method=EntryLog.VerificationMethod.FACE_ONLY,
            status=EntryLog.Status.FAILED,
            gate_location=gate_location,
            failure_reason="Ambiguous match, not resolved by card tap in time.",
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
        someone as Unknown."""
        UnmatchedAttempt.objects.create(gate_location=gate_location, embedding=embedding)

        query = np.array(embedding, dtype=np.float32)
        window_cutoff = timezone.now() - timezone.timedelta(seconds=settings.VOTE_WINDOW_SECONDS)
        recent_attempts = list(
            UnmatchedAttempt.objects.filter(gate_location=gate_location, timestamp__gte=window_cutoff)
            .order_by("-timestamp")[: settings.VOTE_WINDOW_SIZE]
        )
        agreement = sum(
            1
            for attempt in recent_attempts
            if float(np.dot(np.array(attempt.embedding, dtype=np.float32), query))
            >= settings.FACE_MATCH_SIMILARITY_THRESHOLD
        )

        if agreement < settings.VOTE_REQUIRED_AGREEMENT:
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
        cooldown_cutoff = timezone.now() - timezone.timedelta(seconds=settings.UNENROLLED_CAPTURE_COOLDOWN_SECONDS)
        recent_unmatched = EntryLog.objects.filter(
            verification_method=EntryLog.VerificationMethod.FACE_ONLY,
            status=EntryLog.Status.FAILED,
            timestamp__gte=cooldown_cutoff,
        ).exclude(unmatched_encoding__isnull=True)

        query = np.array(embedding, dtype=np.float32)
        for log in recent_unmatched:
            stored = np.array(log.unmatched_encoding, dtype=np.float32)
            similarity = float(np.dot(stored, query))
            if similarity >= settings.FACE_MATCH_SIMILARITY_THRESHOLD:
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
                    payload["captured_photo"] = request.build_absolute_uri(log.captured_photo.url)
                return payload

        captured_photo_bytes = insightface_utils.crop_face(bgr_image, box)
        return self._failure_payload(
            direction, gate_location, reason, best_similarity, captured_photo_bytes, request, embedding, box,
            liveness_score,
        )

    @staticmethod
    def _failure_payload(
        direction, gate_location, reason, similarity=None, captured_photo_bytes=None, request=None,
        embedding=None, box=None, liveness_score=None,
    ):
        log = EntryLog(
            person=None,
            direction=direction,
            verification_method=EntryLog.VerificationMethod.FACE_ONLY,
            status=EntryLog.Status.FAILED,
            gate_location=gate_location,
            failure_reason=reason,
            liveness_score=liveness_score,
        )
        if embedding is not None:
            log.unmatched_encoding = list(embedding)
        if captured_photo_bytes is not None:
            log.captured_photo.save("unenrolled_capture.jpg", ContentFile(captured_photo_bytes), save=False)
        log.save()

        payload = {"success": False, "retry": False, "reason": reason, "person_name": None, "log_id": log.id}
        if similarity is not None:
            payload["similarity"] = similarity
        if liveness_score is not None:
            payload["liveness_score"] = liveness_score
        if box is not None:
            payload["box"] = _box_to_dict(box)
        if log.captured_photo and request is not None:
            payload["captured_photo"] = request.build_absolute_uri(log.captured_photo.url)
        return payload

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
        window_cutoff = timezone.now() - timezone.timedelta(seconds=settings.VOTE_WINDOW_SECONDS)
        recent_attempts = list(
            SpoofAttempt.objects.filter(gate_location=gate_location, timestamp__gte=window_cutoff)
            .order_by("-timestamp")[: settings.VOTE_WINDOW_SIZE]
        )
        agreement = sum(
            1
            for attempt in recent_attempts
            if attempt.liveness_score < settings.LIVENESS_SCORE_THRESHOLD
            and float(np.dot(np.array(attempt.embedding, dtype=np.float32), query))
            >= settings.FACE_MATCH_SIMILARITY_THRESHOLD
        )

        if agreement < settings.VOTE_REQUIRED_AGREEMENT:
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
        cooldown_cutoff = timezone.now() - timezone.timedelta(seconds=settings.SPOOF_CAPTURE_COOLDOWN_SECONDS)
        recent_spoof = EntryLog.objects.filter(
            status=EntryLog.Status.SPOOF_SUSPECTED, timestamp__gte=cooldown_cutoff
        ).exclude(unmatched_encoding__isnull=True)

        query = np.array(embedding, dtype=np.float32)
        for log in recent_spoof:
            stored = np.array(log.unmatched_encoding, dtype=np.float32)
            similarity = float(np.dot(stored, query))
            if similarity >= settings.FACE_MATCH_SIMILARITY_THRESHOLD:
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
                    payload["captured_photo"] = request.build_absolute_uri(log.captured_photo.url)
                return payload

        captured_photo_bytes = insightface_utils.crop_face(bgr_image, box)
        log = EntryLog(
            person=None,
            direction=direction,
            verification_method=EntryLog.VerificationMethod.FACE_ONLY,
            status=EntryLog.Status.SPOOF_SUSPECTED,
            gate_location=gate_location,
            failure_reason=reason,
            liveness_score=liveness_score,
            unmatched_encoding=list(embedding),
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
            "box": _box_to_dict(box),
        }
        if log.captured_photo:
            payload["captured_photo"] = request.build_absolute_uri(log.captured_photo.url)
        return payload

    @staticmethod
    def _transient_payload(reason):
        """Same shape as _failure_payload but doesn't write an EntryLog row -
        for idle states with no face to actually evaluate against anyone."""
        return {"success": False, "reason": reason, "person_name": None, "log_id": None}
