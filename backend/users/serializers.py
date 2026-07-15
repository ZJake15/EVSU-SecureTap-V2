from rest_framework import serializers

from .face_utils import compute_face_encoding, normalize_to_jpeg
from .models import FaceEncoding, Person


class PersonSerializer(serializers.ModelSerializer):
    photo = serializers.ImageField(write_only=True, required=False)
    # Explicit default: DRF's auto-generated BooleanField treats a key that's
    # simply absent from multipart/form-data as False (mirroring unchecked
    # HTML checkboxes) instead of falling back to the model's default=True,
    # so every new registration was silently coming back inactive.
    is_active = serializers.BooleanField(required=False, default=True)

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
            "is_active",
            "created_at",
        ]
        read_only_fields = ["id", "photo_reference", "created_at"]

    def create(self, validated_data):
        photo = validated_data.pop("photo", None)
        if photo is None:
            raise serializers.ValidationError(
                {"photo": "A reference photo is required to register a new person."}
            )
        try:
            encoding = compute_face_encoding(photo)
        except ValueError as exc:
            raise serializers.ValidationError({"photo": str(exc)})

        person = Person.objects.create(photo_reference=normalize_to_jpeg(photo), **validated_data)
        FaceEncoding.objects.create(person=person, encoding=encoding.tolist())
        return person

    def update(self, instance, validated_data):
        photo = validated_data.pop("photo", None)
        if photo is not None:
            try:
                encoding = compute_face_encoding(photo)
            except ValueError as exc:
                raise serializers.ValidationError({"photo": str(exc)})
            instance.photo_reference = normalize_to_jpeg(photo)
            FaceEncoding.objects.update_or_create(
                person=instance, defaults={"encoding": encoding.tolist()}
            )

        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        return instance
