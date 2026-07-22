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
        # A face match was ambiguous (borderline confidence or two close
        # candidates) and got resolved by a card tap requested specifically
        # to break that tie - distinct from NFC_ONLY (a plain lookup) and
        # NFC_AND_FACE (the old, no-longer-used dual-check flow).
        FACE_AND_CARD_TIEBREAK = "face_and_card_tiebreak", "Face + card tiebreak"

    class Status(models.TextChoices):
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    # Nullable: an unrecognized NFC tap (no matching Person) must still be logged.
    person = models.ForeignKey(
        Person, on_delete=models.SET_NULL, null=True, blank=True, related_name="entry_logs"
    )
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    direction = models.CharField(max_length=10, choices=Direction.choices, default=Direction.ENTRY)
    verification_method = models.CharField(max_length=24, choices=VerificationMethod.choices)
    status = models.CharField(max_length=10, choices=Status.choices)
    gate_location = models.CharField(max_length=100)
    failure_reason = models.CharField(max_length=255, blank=True)
    # A cropped photo of the face as actually seen at the gate - only saved for
    # unrecognized faces (no matching enrolled Person), so a guard/admin can
    # see who was denied instead of just a text reason.
    captured_photo = models.ImageField(upload_to="unenrolled_captures/%Y/%m/%d/", null=True, blank=True)
    # The 512-d ArcFace vector for an unrecognized face, saved only on
    # FAILED/FACE_ONLY rows so a later unmatched scan can be compared against
    # it to tell "the same stranger lingering" apart from "a different
    # stranger" - there's no Person to key a cooldown on otherwise.
    unmatched_encoding = models.JSONField(null=True, blank=True)
    # The match similarity (0-1, ArcFace cosine similarity) at the moment
    # this row was confirmed - null for NFC-only rows and pre-embedding
    # historical rows. Kept for the thesis's threshold/accuracy evaluation.
    match_confidence = models.FloatField(null=True, blank=True)

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self):
        who = self.person.full_name if self.person else "Unknown"
        return f"{who} - {self.direction} - {self.status} @ {self.timestamp}"


class RecognitionAttempt(models.Model):
    """One raw per-frame face match from the continuous camera scan - kept
    only briefly (a rolling few-second window) so IdentifyView can require
    the same person to be the top match across several recent frames before
    confirming a real EntryLog row, instead of trusting a single frame.
    Not a permanent audit record the way EntryLog is - old rows can be
    pruned freely."""

    gate_location = models.CharField(max_length=100)
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="recognition_attempts")
    similarity = models.FloatField()
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)


class UnmatchedAttempt(models.Model):
    """The unmatched-face counterpart to RecognitionAttempt: one sharp,
    fully-in-frame face from the continuous scan that didn't match anyone.
    There's no Person to key voting on the way a match has, so IdentifyView
    instead groups recent rows by embedding similarity ("probably the same
    unrecognized face across frames") and only confirms a real FAILED
    EntryLog once enough of them agree within the window - the same
    grace-period treatment a match already gets, so one blurry or
    still-mid-blink frame can't alone flag someone as Unknown. Not a
    permanent audit record - old rows can be pruned freely."""

    gate_location = models.CharField(max_length=100)
    embedding = models.JSONField()
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)


class PendingTiebreak(models.Model):
    """An in-progress "please tap your card" prompt for one gate: the camera
    scan saw a borderline or ambiguous face match and is waiting (up to
    TIEBREAK_TIMEOUT_SECONDS) for a card tap to say which of the close
    candidates it actually is. At most one active tiebreak per gate."""

    gate_location = models.CharField(max_length=100, unique=True)
    candidate_person_ids = models.JSONField()
    direction = models.CharField(max_length=10)
    created_at = models.DateTimeField(auto_now_add=True)
