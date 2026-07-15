import io

import face_recognition
import numpy as np
from django.conf import settings
from django.core.files.base import ContentFile
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django_filters.rest_framework import DjangoFilterBackend
from PIL import Image
from rest_framework import status as http_status
from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import HasServiceToken, IsSecurityOrAbove
from users.models import Person

from .filters import EntryLogFilter
from .models import EntryLog
from .serializers import EntryLogSerializer, IdentifyRequestSerializer, VerifyRequestSerializer


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
    """Called when a card is tapped. This is now a lookup, not a face check:
    the continuous camera scan (IdentifyView) handles actual identification,
    so tapping a card just shows the guard the on-file photo for a manual
    cross-check, or an error if the card isn't linked to an enrolled person."""

    permission_classes = [HasServiceToken]

    def post(self, request):
        serializer = VerifyRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        nfc_id = data["nfc_id"]
        direction = data["direction"]
        gate_location = data["gate_location"]

        try:
            person = Person.objects.get(nfc_id=nfc_id, is_active=True)
        except Person.DoesNotExist:
            return self._log_and_respond(
                person=None,
                direction=direction,
                gate_location=gate_location,
                success=False,
                reason="NFC ID not recognized or inactive.",
            )

        return self._log_and_respond(
            person=person,
            direction=direction,
            gate_location=gate_location,
            success=True,
            reason=None,
        )

    def _log_and_respond(self, *, person, direction, gate_location, success, reason):
        log = EntryLog.objects.create(
            person=person,
            direction=direction,
            verification_method=EntryLog.VerificationMethod.NFC_ONLY,
            status=EntryLog.Status.SUCCESS if success else EntryLog.Status.FAILED,
            gate_location=gate_location,
            failure_reason=reason or "",
        )
        return Response(
            {
                "success": success,
                "reason": reason,
                "log_id": log.id,
                **person_payload(person, self.request),
            },
            status=http_status.HTTP_200_OK,
        )


class IdentifyView(APIView):
    """Called periodically by the entry-agent's continuous camera scan. There's
    no claimed identity here (unlike VerifyView) - every face detected in the
    frame is compared independently against every enrolled active person (a
    1:N search per face), so several people walking through together in the
    same frame are each identified, not just one. The closest match within
    FACE_MATCH_THRESHOLD wins for each face. A person recognized again within
    RECOGNITION_COOLDOWN_SECONDS reuses their existing log row instead of
    creating a new one, so someone lingering at the gate doesn't spam the log.

    Matching is a linear scan over every enrolled encoding - fast enough
    (it's a vectorized numpy comparison) for hundreds of enrolled people, but
    won't scale gracefully to a huge student body without a proper vector
    index. That's an acceptable limit for this project's scope.
    """

    permission_classes = [HasServiceToken]

    def post(self, request):
        serializer = IdentifyRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        direction = data["direction"]
        gate_location = data["gate_location"]
        image_file = data["image"]

        try:
            image = face_recognition.load_image_file(image_file)
            # Upsampling once (the default is 0 extra passes) roughly doubles
            # the image before scanning for faces, which matters a lot for
            # someone several feet from the camera - their face is a small
            # cluster of pixels in the raw frame, and the HOG detector misses
            # small faces far more often than close-up ones. Costs some CPU
            # time per scan, but recognizing people at a normal walking
            # distance from the gate is the whole point of this endpoint.
            face_locations = face_recognition.face_locations(image, number_of_times_to_upsample=2)
            captured_encodings = face_recognition.face_encodings(image, known_face_locations=face_locations)
        except Exception:
            captured_encodings = []
            face_locations = []

        if not captured_encodings:
            # Nobody's in frame - this is the idle state for the vast majority of
            # scan cycles, not a security-relevant event, so it isn't logged.
            # Logging it would flood EntryLog with a row every ~0.2s any time the
            # gate is simply unattended, burying genuine successes/failures.
            return Response({"results": [self._transient_payload("No face detected.")]})

        candidates = [
            person
            for person in Person.objects.filter(is_active=True).select_related("face_encoding")
            if hasattr(person, "face_encoding")
        ]
        if not candidates:
            # A setup/config gap (nothing enrolled yet), not a per-frame security
            # event - same reasoning as above, don't log every idle cycle.
            payload = self._transient_payload("No enrolled faces to compare against.")
            return Response({"results": [payload]})

        known_encodings = [np.array(person.face_encoding.encoding) for person in candidates]
        results = [
            self._match_one_face(
                face_encoding, candidates, known_encodings, direction, gate_location, request, image, location
            )
            for face_encoding, location in zip(captured_encodings, face_locations)
        ]
        return Response({"results": results})

    def _match_one_face(
        self, face_encoding, candidates, known_encodings, direction, gate_location, request, image, location
    ):
        distances = face_recognition.face_distance(known_encodings, face_encoding)
        best_index = int(np.argmin(distances))
        best_distance = float(distances[best_index])

        if best_distance > settings.FACE_MATCH_THRESHOLD:
            return self._handle_unmatched_face(
                face_encoding, image, location, direction, gate_location, request, best_distance
            )

        matched_person = candidates[best_index]
        cooldown_cutoff = timezone.now() - timezone.timedelta(seconds=settings.RECOGNITION_COOLDOWN_SECONDS)
        recent_log = (
            EntryLog.objects.filter(
                person=matched_person,
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
                "reason": None,
                "log_id": recent_log.id,
                "distance": best_distance,
                "deduped": True,
                **person_payload(matched_person, request),
            }

        log = EntryLog.objects.create(
            person=matched_person,
            direction=direction,
            verification_method=EntryLog.VerificationMethod.FACE_ONLY,
            status=EntryLog.Status.SUCCESS,
            gate_location=gate_location,
        )
        return {
            "success": True,
            "reason": None,
            "log_id": log.id,
            "distance": best_distance,
            **person_payload(matched_person, request),
        }

    def _handle_unmatched_face(self, face_encoding, image, location, direction, gate_location, request, best_distance):
        """An unrecognized face has no Person to key a cooldown on the way a
        matched face does, so instead this compares the new encoding against
        every unmatched face logged in the last UNENROLLED_CAPTURE_COOLDOWN_SECONDS:
        close enough (within FACE_MATCH_THRESHOLD) counts as "the same
        stranger still standing there" and reuses that log row rather than
        creating a new one and capturing another photo - this is what keeps a
        lingering unrecognized person from flooding the log/Live Monitoring."""
        reason = "Not enrolled - no matching student/staff record."
        cooldown_cutoff = timezone.now() - timezone.timedelta(seconds=settings.UNENROLLED_CAPTURE_COOLDOWN_SECONDS)
        recent_unmatched = EntryLog.objects.filter(
            verification_method=EntryLog.VerificationMethod.FACE_ONLY,
            status=EntryLog.Status.FAILED,
            timestamp__gte=cooldown_cutoff,
        ).exclude(unmatched_encoding__isnull=True)

        for log in recent_unmatched:
            distance = face_recognition.face_distance([np.array(log.unmatched_encoding)], face_encoding)[0]
            if distance <= settings.FACE_MATCH_THRESHOLD:
                payload = {
                    "success": False,
                    "reason": reason,
                    "person_name": None,
                    "log_id": log.id,
                    "distance": best_distance,
                    "deduped": True,
                }
                if log.captured_photo:
                    payload["captured_photo"] = request.build_absolute_uri(log.captured_photo.url)
                return payload

        captured_photo = self._crop_face(image, location)
        return self._failure_payload(
            direction, gate_location, reason, best_distance, captured_photo, request, face_encoding
        )

    @staticmethod
    def _failure_payload(
        direction, gate_location, reason, distance=None, captured_photo=None, request=None, encoding=None
    ):
        log = EntryLog(
            person=None,
            direction=direction,
            verification_method=EntryLog.VerificationMethod.FACE_ONLY,
            status=EntryLog.Status.FAILED,
            gate_location=gate_location,
            failure_reason=reason,
        )
        if encoding is not None:
            log.unmatched_encoding = encoding.tolist()
        if captured_photo is not None:
            log.captured_photo.save(captured_photo.name, captured_photo, save=False)
        log.save()

        payload = {"success": False, "reason": reason, "person_name": None, "log_id": log.id}
        if distance is not None:
            payload["distance"] = distance
        if log.captured_photo and request is not None:
            payload["captured_photo"] = request.build_absolute_uri(log.captured_photo.url)
        return payload

    @staticmethod
    def _transient_payload(reason):
        """Same shape as _failure_payload but doesn't write an EntryLog row -
        for idle states with no face to actually evaluate against anyone."""
        return {"success": False, "reason": reason, "person_name": None, "log_id": None}

    @staticmethod
    def _crop_face(image, location, padding_ratio=0.4):
        """Crops the detected face out of the full frame (with a margin so the
        guard sees more than just eyes-nose-mouth) and returns it as a JPEG
        ContentFile ready to attach to an EntryLog."""
        top, right, bottom, left = location
        height, width = image.shape[:2]
        pad_y = int((bottom - top) * padding_ratio)
        pad_x = int((right - left) * padding_ratio)
        top = max(0, top - pad_y)
        left = max(0, left - pad_x)
        bottom = min(height, bottom + pad_y)
        right = min(width, right + pad_x)

        buffer = io.BytesIO()
        Image.fromarray(image[top:bottom, left:right]).save(buffer, format="JPEG", quality=85)
        return ContentFile(buffer.getvalue(), name="unenrolled_capture.jpg")
