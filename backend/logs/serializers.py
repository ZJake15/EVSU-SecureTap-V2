from rest_framework import serializers

from securetap_project.media_auth import build_signed_media_url

from .models import EntryLog


class EntryLogSerializer(serializers.ModelSerializer):
    person_name = serializers.SerializerMethodField()
    person_photo = serializers.SerializerMethodField()
    student_or_employee_id = serializers.SerializerMethodField()
    captured_photo = serializers.SerializerMethodField()
    performed_by_username = serializers.SerializerMethodField()
    distinguishing_note = serializers.SerializerMethodField()
    on_duty_name = serializers.SerializerMethodField()

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
            "match_confidence",
            "liveness_score",
            "occlusion_detected",
            "performed_by_username",
            "distinguishing_note",
            "on_duty_name",
            "unattended",
        ]

    def get_person_name(self, obj):
        return obj.person.full_name if obj.person else None

    def get_person_photo(self, obj):
        if obj.person and obj.person.photo_reference:
            request = self.context.get("request")
            return build_signed_media_url(request, obj.person.photo_reference.url)
        return None

    def get_student_or_employee_id(self, obj):
        return obj.person.student_or_employee_id if obj.person else None

    def get_captured_photo(self, obj):
        if obj.captured_photo:
            request = self.context.get("request")
            return build_signed_media_url(request, obj.captured_photo.url)
        return None

    def get_performed_by_username(self, obj):
        # Only ever set on a MANUAL_OVERRIDE row - this is what the Logs UI
        # keys off to show "manually logged by <username>" distinctly from
        # an automatic face/NFC match, which has no dashboard account behind
        # it at all.
        return obj.performed_by.username if obj.performed_by else None

    def get_distinguishing_note(self, obj):
        # A manual backstop for a confusable pair (see users.models.
        # ConfusablePair) - never used by matching, just handed through so a
        # CONFUSABLE_PAIR_TIEBREAK row can show it to whoever's reviewing.
        if not obj.person:
            return None
        return obj.person.distinguishing_note or None

    def get_on_duty_name(self, obj):
        # The guard signed in at the gate monitor when this row was written
        # (Settings -> "Guards sign in at the gate monitor"); see
        # `unattended` for "sign-in was on but nobody was signed in".
        if not obj.on_duty:
            return None
        return obj.on_duty.get_full_name() or obj.on_duty.username


class GateSignInRequestSerializer(serializers.Serializer):
    """A guard signing in at the gate monitor with their password (a staff
    ID card tap goes through /api/verify like any other tap)."""

    gate_location = serializers.CharField(max_length=100)
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(max_length=128, trim_whitespace=False)


class GateSignOutRequestSerializer(serializers.Serializer):
    """Ends the shift at this gate - shift_id (the one the gate monitor
    started) when given, otherwise whatever is open there."""

    gate_location = serializers.CharField(max_length=100)
    shift_id = serializers.IntegerField(required=False)
    reason = serializers.ChoiceField(choices=["signed_out", "gate_closed", "gate_reopened"], default="signed_out")


class ManualOverrideRequestSerializer(serializers.Serializer):
    """A Security Officer logging an entry by hand after visually checking a
    physical ID, because the scanner itself failed - see logs/views.py's
    ManualOverrideView. Deliberately its own serializer/endpoint, not a
    variant of VerifyRequestSerializer (the card-tap/typed-ID lookup): that
    one is the entry-agent authenticating with a shared service token, on
    behalf of no one in particular; this one is a specific, logged-in
    dashboard account making a judgment call, and needs a reason (what did
    they check?) that a card lookup never does."""

    student_or_employee_id = serializers.CharField(max_length=50)
    direction = serializers.ChoiceField(choices=EntryLog.Direction.choices, default=EntryLog.Direction.ENTRY)
    reason = serializers.CharField(max_length=500)


class VerifyRequestSerializer(serializers.Serializer):
    """A card tap is a lookup, not a face check - no image involved. The
    entry-agent's manual ID-entry fallback (for when the reader itself
    fails) hits this same endpoint with student_or_employee_id instead of
    nfc_id - exactly one of the two must be provided."""

    nfc_id = serializers.CharField(max_length=100, required=False, allow_blank=True)
    student_or_employee_id = serializers.CharField(max_length=50, required=False, allow_blank=True)
    gate_location = serializers.CharField(max_length=100)
    direction = serializers.ChoiceField(
        choices=EntryLog.Direction.choices, default=EntryLog.Direction.ENTRY
    )
    # True for a tap the entry-agent saved while offline and is sending
    # later - a staff card tap like that must not sign anyone in now.
    replayed = serializers.BooleanField(required=False, default=False)

    def validate(self, data):
        if not data.get("nfc_id") and not data.get("student_or_employee_id"):
            raise serializers.ValidationError(
                "Either nfc_id or student_or_employee_id is required."
            )
        return data


class IdentifyRequestSerializer(serializers.Serializer):
    """A sampled frame from the continuous camera scan - no claimed identity."""

    gate_location = serializers.CharField(max_length=100)
    direction = serializers.ChoiceField(
        choices=EntryLog.Direction.choices, default=EntryLog.Direction.ENTRY
    )
    image = serializers.ImageField()
