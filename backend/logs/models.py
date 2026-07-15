from django.db import models

from users.models import Person


class EntryLog(models.Model):
    class Direction(models.TextChoices):
        ENTRY = "entry", "Entry"
        EXIT = "exit", "Exit"

    class VerificationMethod(models.TextChoices):
        # Kept for historical rows - the dual-check tap flow was replaced by
        # continuous face scanning (FACE_ONLY) plus an optional NFC lookup
        # (NFC_ONLY), so new rows no longer use this value.
        NFC_AND_FACE = "nfc_and_face", "NFC + Face"
        NFC_ONLY = "nfc_only", "NFC only"
        FACE_ONLY = "face_only", "Face scan"
        MANUAL_OVERRIDE = "manual_override", "Manual override"

    class Status(models.TextChoices):
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    # Nullable: an unrecognized NFC tap (no matching Person) must still be logged.
    person = models.ForeignKey(
        Person, on_delete=models.SET_NULL, null=True, blank=True, related_name="entry_logs"
    )
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    direction = models.CharField(max_length=10, choices=Direction.choices, default=Direction.ENTRY)
    verification_method = models.CharField(max_length=20, choices=VerificationMethod.choices)
    status = models.CharField(max_length=10, choices=Status.choices)
    gate_location = models.CharField(max_length=100)
    failure_reason = models.CharField(max_length=255, blank=True)
    # A cropped photo of the face as actually seen at the gate - only saved for
    # unrecognized faces (no matching enrolled Person), so a guard/admin can
    # see who was denied instead of just a text reason.
    captured_photo = models.ImageField(upload_to="unenrolled_captures/%Y/%m/%d/", null=True, blank=True)
    # The 128-d face_recognition vector for an unrecognized face, saved only on
    # FAILED/FACE_ONLY rows so a later unmatched scan can be compared against
    # it to tell "the same stranger lingering" apart from "a different
    # stranger" - there's no Person to key a cooldown on otherwise.
    unmatched_encoding = models.JSONField(null=True, blank=True)

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self):
        who = self.person.full_name if self.person else "Unknown"
        return f"{who} - {self.direction} - {self.status} @ {self.timestamp}"
