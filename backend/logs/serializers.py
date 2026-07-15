from rest_framework import serializers

from .models import EntryLog


class EntryLogSerializer(serializers.ModelSerializer):
    person_name = serializers.SerializerMethodField()
    person_photo = serializers.SerializerMethodField()
    student_or_employee_id = serializers.SerializerMethodField()
    captured_photo = serializers.SerializerMethodField()

    class Meta:
        model = EntryLog
        fields = [
            "id",
            "person",
            "person_name",
            "person_photo",
            "student_or_employee_id",
            "captured_photo",
            "timestamp",
            "direction",
            "verification_method",
            "status",
            "gate_location",
            "failure_reason",
        ]

    def get_person_name(self, obj):
        return obj.person.full_name if obj.person else None

    def get_person_photo(self, obj):
        if obj.person and obj.person.photo_reference:
            request = self.context.get("request")
            url = obj.person.photo_reference.url
            return request.build_absolute_uri(url) if request else url
        return None

    def get_student_or_employee_id(self, obj):
        return obj.person.student_or_employee_id if obj.person else None

    def get_captured_photo(self, obj):
        if obj.captured_photo:
            request = self.context.get("request")
            url = obj.captured_photo.url
            return request.build_absolute_uri(url) if request else url
        return None


class VerifyRequestSerializer(serializers.Serializer):
    """A card tap is now a lookup, not a face check - no image involved."""

    nfc_id = serializers.CharField(max_length=100)
    gate_location = serializers.CharField(max_length=100)
    direction = serializers.ChoiceField(
        choices=EntryLog.Direction.choices, default=EntryLog.Direction.ENTRY
    )


class IdentifyRequestSerializer(serializers.Serializer):
    """A sampled frame from the continuous camera scan - no claimed identity."""

    gate_location = serializers.CharField(max_length=100)
    direction = serializers.ChoiceField(
        choices=EntryLog.Direction.choices, default=EntryLog.Direction.ENTRY
    )
    image = serializers.ImageField()
