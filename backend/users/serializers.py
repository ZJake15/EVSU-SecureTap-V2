from rest_framework import serializers

from .confusable_utils import (
    MAX_EMBEDDINGS_PER_CONFUSABLE_PERSON,
    find_confusable_candidates,
    get_confusable_partner_ids,
    has_confusable_flag,
    record_confusable_pairs,
)
from .insightface_utils import compute_face_embedding, ensure_supported_image_format, normalize_to_jpeg
from .models import ConfusablePair, DeactivationRequest, FaceEmbedding, Person

# A guided multi-photo enrollment captures 3-5 near-frontal shots with slight
# variation - matching gets no better past that, and each embedding is a
# comparison the gate-scan has to do for every enrolled person, every frame.
# A person flagged in a confusable pair gets a higher ceiling instead - see
# MAX_EMBEDDINGS_PER_CONFUSABLE_PERSON in confusable_utils.py.
MAX_EMBEDDINGS_PER_PERSON = 5


def max_embeddings_for(person):
    return MAX_EMBEDDINGS_PER_CONFUSABLE_PERSON if has_confusable_flag(person.id) else MAX_EMBEDDINGS_PER_PERSON


class FaceEmbeddingSerializer(serializers.ModelSerializer):
    """Read-only - shown on a Person so the dashboard can display "3/5
    photos" and thumbnails without exposing the raw 512-d vector itself."""

    source_image = serializers.SerializerMethodField()

    class Meta:
        model = FaceEmbedding
        fields = ["id", "source_image", "detection_score", "is_low_confidence", "created_at"]

    def get_source_image(self, obj):
        if not obj.source_image:
            return None
        request = self.context.get("request")
        url = obj.source_image.url
        return request.build_absolute_uri(url) if request else url


class PersonSerializer(serializers.ModelSerializer):
    photo = serializers.ImageField(write_only=True, required=False)
    # A separate, optional upload for the guard/admin-facing display photo -
    # deliberately independent of `photo`/face_embeddings above. `photo` is
    # matching data (must pass face detection/quality checks); this is just
    # what shows on a card-tap result or the gate-scan log, so it doesn't
    # need to contain a detectable face at all (an ID scan, a nicer portrait,
    # whatever). Without one, photo_reference falls back to the enrollment
    # photo, same as before this field existed.
    profile_picture = serializers.ImageField(write_only=True, required=False)
    # Explicit default: DRF's auto-generated BooleanField treats a key that's
    # simply absent from multipart/form-data as False (mirroring unchecked
    # HTML checkboxes) instead of falling back to the model's default=True,
    # so every new registration was silently coming back inactive.
    is_active = serializers.BooleanField(required=False, default=True)
    face_embeddings = FaceEmbeddingSerializer(many=True, read_only=True)
    # Set by the dashboard's explicit single-photo fallback path (guided
    # multi-shot capture omits this, so it defaults False) - flags the
    # resulting embedding as lower-confidence, same as a bulk-imported one,
    # since both share the same underlying weakness: one uncorroborated photo.
    fallback_enrollment = serializers.BooleanField(write_only=True, required=False, default=False)
    # True while a SASO-submitted deactivation request on this person is still
    # awaiting an Admin's approve/reject - lets the Users page show a "Pending
    # deactivation" badge instead of the record just silently staying active
    # with no visible explanation of why a SASO's delete attempt didn't stick.
    pending_deactivation = serializers.SerializerMethodField()
    # Every other Person this one is currently flagged confusable with -
    # always live, not just a one-time "just detected this" toast, since the
    # risk doesn't go away after enrollment (see ConfusablePair's docstring).
    # An enrollment endpoint that just created/added a photo for this person
    # can show the exact same field's contents as a "please confirm this is
    # expected" prompt - no separate one-time-warning field needed.
    confusable_partners = serializers.SerializerMethodField()
    # How many enrollment photos this specific person can have - higher than
    # the standard cap once they're in a confusable pair (see
    # confusable_utils.MAX_EMBEDDINGS_PER_CONFUSABLE_PERSON). Exposed so the
    # dashboard never has to duplicate/guess the two numbers itself.
    max_embeddings = serializers.SerializerMethodField()

    class Meta:
        model = Person
        fields = [
            "id",
            "full_name",
            "role",
            "student_or_employee_id",
            "nfc_id",
            "department_or_course",
            "photo_reference",
            "photo",
            "profile_picture",
            "is_active",
            "created_at",
            "face_embeddings",
            "fallback_enrollment",
            "pending_deactivation",
            "distinguishing_note",
            "confusable_partners",
            "max_embeddings",
        ]
        read_only_fields = ["id", "photo_reference", "created_at"]

    def get_pending_deactivation(self, obj):
        return obj.deactivation_requests.filter(status=DeactivationRequest.Status.PENDING).exists()

    def get_confusable_partners(self, obj):
        partner_ids = get_confusable_partner_ids(obj.id)
        if not partner_ids:
            return []
        partners = Person.objects.filter(id__in=partner_ids)
        return [
            {"id": partner.id, "full_name": partner.full_name, "student_or_employee_id": partner.student_or_employee_id}
            for partner in partners
        ]

    def get_max_embeddings(self, obj):
        return max_embeddings_for(obj)

    # Format is checked per-field rather than inside create()/update() so DRF
    # returns it as a normal {"photo": [...]} field error the dashboard already
    # knows how to surface, and so it's rejected before anything is written or
    # any face detection runs. ImageField alone isn't enough - it only asks
    # Pillow "is this an image at all", which a WEBP or BMP passes happily.
    @staticmethod
    def _validate_image_format(value):
        try:
            ensure_supported_image_format(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc))
        return value

    def validate_photo(self, value):
        return self._validate_image_format(value)

    def validate_profile_picture(self, value):
        # Applies to the display photo too: it never goes through face
        # detection, so this is the only thing standing between an unsupported
        # upload and a stored file the dashboard then can't render.
        return self._validate_image_format(value)

    def create(self, validated_data):
        photo = validated_data.pop("photo", None)
        profile_picture = validated_data.pop("profile_picture", None)
        is_low_confidence = validated_data.pop("fallback_enrollment", False)
        if photo is None:
            raise serializers.ValidationError(
                {"photo": "A reference photo is required to register a new person."}
            )
        try:
            embedding, det_score = compute_face_embedding(photo)
        except ValueError as exc:
            raise serializers.ValidationError({"photo": str(exc)})

        # profile_picture, if given, wins as the display photo - otherwise
        # fall back to the enrollment photo so nobody ends up with a blank one.
        display_photo = profile_picture if profile_picture is not None else photo
        person = Person.objects.create(photo_reference=normalize_to_jpeg(display_photo), **validated_data)
        photo.seek(0)
        FaceEmbedding.objects.create(
            person=person, embedding=embedding, source_image=photo, detection_score=det_score,
            is_low_confidence=is_low_confidence,
        )
        # Checked on every new embedding, not just this first one - the
        # guided capture flow adds 4 more photos right after this via
        # add_photo(), each of which runs this same check (see PersonViewSet.
        # add_photo), so a confusable match revealed only by a later photo
        # still gets caught, not just one revealed by the very first shot.
        matches = find_confusable_candidates(embedding, exclude_person_id=person.id)
        if matches:
            record_confusable_pairs(person, matches)
        return person

    def update(self, instance, validated_data):
        photo = validated_data.pop("photo", None)
        profile_picture = validated_data.pop("profile_picture", None)
        validated_data.pop("fallback_enrollment", None)  # only meaningful at creation time
        if photo is not None:
            cap = max_embeddings_for(instance)
            if instance.face_embeddings.count() >= cap:
                raise serializers.ValidationError(
                    {"photo": f"Already has the maximum of {cap} enrollment photos."}
                )
            try:
                embedding, det_score = compute_face_embedding(photo)
            except ValueError as exc:
                raise serializers.ValidationError({"photo": str(exc)})
            instance.photo_reference = normalize_to_jpeg(photo)
            photo.seek(0)
            FaceEmbedding.objects.create(
                person=instance, embedding=embedding, source_image=photo, detection_score=det_score,
            )
            matches = find_confusable_candidates(embedding, exclude_person_id=instance.id)
            if matches:
                record_confusable_pairs(instance, matches)

        if profile_picture is not None:
            # Explicit upload always wins, even over the `photo` branch above.
            instance.photo_reference = normalize_to_jpeg(profile_picture)

        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        return instance


class DeactivationRequestSerializer(serializers.ModelSerializer):
    """Read-only view of the maker-checker state - creation happens through
    PersonViewSet.destroy() (a SASO's DELETE becomes a request instead of an
    immediate change) and resolution through DeactivationRequestViewSet's
    approve/reject actions, not through this serializer directly."""

    person_name = serializers.CharField(source="person.full_name", read_only=True)
    student_or_employee_id = serializers.CharField(source="person.student_or_employee_id", read_only=True)
    requested_by_username = serializers.SerializerMethodField()
    resolved_by_username = serializers.SerializerMethodField()

    class Meta:
        model = DeactivationRequest
        fields = [
            "id",
            "person",
            "person_name",
            "student_or_employee_id",
            "requested_by_username",
            "requested_at",
            "reason",
            "status",
            "resolved_by_username",
            "resolved_at",
            "resolution_note",
        ]
        read_only_fields = fields

    def get_requested_by_username(self, obj):
        return obj.requested_by.username if obj.requested_by else None

    def get_resolved_by_username(self, obj):
        return obj.resolved_by.username if obj.resolved_by else None


class ConfusablePairSerializer(serializers.ModelSerializer):
    """Covers both how a pair got flagged: auto-detected ones are created by
    confusable_utils.record_confusable_pairs (read-only from here - person_a/
    person_b/source/detected_similarity are never client-writable for those),
    and a manually-flagged one is created directly through this serializer's
    create() (see ConfusablePairViewSet), which always stamps source=MANUAL
    and flagged_by=the requesting user - a client can never claim a pair is
    auto-detected or attribute it to somebody else."""

    person_a_name = serializers.CharField(source="person_a.full_name", read_only=True)
    person_b_name = serializers.CharField(source="person_b.full_name", read_only=True)
    flagged_by_username = serializers.SerializerMethodField()

    class Meta:
        model = ConfusablePair
        fields = [
            "id",
            "person_a",
            "person_a_name",
            "person_b",
            "person_b_name",
            "source",
            "detected_similarity",
            "flagged_by_username",
            "created_at",
        ]
        read_only_fields = ["id", "source", "detected_similarity", "flagged_by_username", "created_at"]

    def get_flagged_by_username(self, obj):
        return obj.flagged_by.username if obj.flagged_by else None

    def validate(self, data):
        person_a = data.get("person_a") or getattr(self.instance, "person_a", None)
        person_b = data.get("person_b") or getattr(self.instance, "person_b", None)
        if person_a and person_b and person_a.id == person_b.id:
            raise serializers.ValidationError("A person can't be flagged as confusable with themselves.")
        return data

    def create(self, validated_data):
        request = self.context.get("request")
        pair, created = ConfusablePair.objects.get_or_create_pair(
            validated_data["person_a"], validated_data["person_b"],
            source=ConfusablePair.Source.MANUAL,
            flagged_by=request.user if request else None,
        )
        # Transient, not a model field - lets the view's perform_create tell
        # a genuine new flag apart from a duplicate POST that just found the
        # pair already on record, so the audit log doesn't claim an action
        # happened when nothing actually changed.
        pair.was_newly_created = created
        return pair
