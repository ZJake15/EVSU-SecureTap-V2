from rest_framework import serializers

from .insightface_utils import compute_face_embedding, normalize_to_jpeg
from .models import FaceEmbedding, Person

# A guided multi-photo enrollment captures 3-5 near-frontal shots with slight
# variation - matching gets no better past that, and each embedding is a
# comparison the gate-scan has to do for every enrolled person, every frame.
MAX_EMBEDDINGS_PER_PERSON = 5


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
        ]
        read_only_fields = ["id", "photo_reference", "created_at"]

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
        return person

    def update(self, instance, validated_data):
        photo = validated_data.pop("photo", None)
        profile_picture = validated_data.pop("profile_picture", None)
        validated_data.pop("fallback_enrollment", None)  # only meaningful at creation time
        if photo is not None:
            if instance.face_embeddings.count() >= MAX_EMBEDDINGS_PER_PERSON:
                raise serializers.ValidationError(
                    {"photo": f"Already has the maximum of {MAX_EMBEDDINGS_PER_PERSON} enrollment photos."}
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

        if profile_picture is not None:
            # Explicit upload always wins, even over the `photo` branch above.
            instance.photo_reference = normalize_to_jpeg(profile_picture)

        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        return instance
