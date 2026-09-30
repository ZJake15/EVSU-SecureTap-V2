from django.conf import settings
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
        # The top face match was confidently, unambiguously itself - and was
        # STILL sent to a card tap, because the matched person is on record
        # (see users.models.ConfusablePair) as unusually similar to someone
        # else, e.g. identical twins. Deliberately its own value, not folded
        # into FACE_AND_CARD_TIEBREAK: that one means the match itself looked
        # uncertain; this one means the match looked fine and was overridden
        # anyway, which is a very different thing to see in the data.
        CONFUSABLE_PAIR_TIEBREAK = "confusable_pair_tiebreak", "Confusable pair tiebreak"

    class Status(models.TextChoices):
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
        # A face passed detection/quality checks but failed the passive
        # liveness (anti-spoofing) check before ever being compared against
        # enrolled embeddings - kept distinct from FAILED (a clean scan that
        # simply didn't match anyone) since this is a security event, not a
        # recognition miss. See users/liveness_utils.py.
        SPOOF_SUSPECTED = "spoof_suspected", "Spoof suspected"
        # The mouth/nose read as covered (hand, mask, high collar) before the
        # face was ever compared against enrolled embeddings - kept distinct
        # from FAILED for the same reason SPOOF_SUSPECTED is: an occluded
        # face isn't a clean non-match, ArcFace was never given a fair look
        # at it, so calling it "Unknown" would be wrong. See
        # users/insightface_utils.mouth_visibility_ratio.
        OCCLUSION_DETECTED = "occlusion_detected", "Occlusion detected"

    # Nullable: an unrecognized NFC tap (no matching Person) must still be logged.
    person = models.ForeignKey(
        Person, on_delete=models.SET_NULL, null=True, blank=True, related_name="entry_logs"
    )
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    direction = models.CharField(max_length=10, choices=Direction.choices, default=Direction.ENTRY)
    verification_method = models.CharField(max_length=24, choices=VerificationMethod.choices)
    status = models.CharField(max_length=20, choices=Status.choices)
    gate_location = models.CharField(max_length=100)
    failure_reason = models.CharField(max_length=255, blank=True)
    # A cropped photo of the face as actually seen at the gate - only saved for
    # unrecognized faces (no matching enrolled Person), so a guard/admin can
    # see who was denied instead of just a text reason.
    captured_photo = models.ImageField(upload_to="unenrolled_captures/%Y/%m/%d/", null=True, blank=True)
    # The 512-d ArcFace vector for an unrecognized or spoof-suspected face,
    # saved only on FAILED/SPOOF_SUSPECTED FACE_ONLY rows so a later scan can
    # be compared against it to tell "the same stranger/attempt still there"
    # apart from "a different one" - there's no Person to key a cooldown on
    # otherwise.
    unmatched_encoding = models.JSONField(null=True, blank=True)
    # The match similarity (0-1, ArcFace cosine similarity) at the moment
    # this row was confirmed - null for NFC-only rows and pre-embedding
    # historical rows. Kept for the thesis's threshold/accuracy evaluation.
    match_confidence = models.FloatField(null=True, blank=True)
    # Passive liveness/anti-spoofing score (0-1, higher = more likely a real,
    # live face) at the moment this row was confirmed - null for NFC-only
    # rows and rows created before this check existed. See
    # settings.LIVENESS_SCORE_THRESHOLD and users/liveness_utils.py.
    liveness_score = models.FloatField(null=True, blank=True)
    # True if an occluded frame (mouth/nose covered) was seen at this gate in
    # the moments before this row was written - regardless of how the row
    # itself concluded. Sits alongside status rather than replacing it: a
    # SUCCESS row with this True means "matched fine, but their face was
    # briefly covered a moment earlier in this same encounter" - worth
    # keeping visible rather than silently dropped once the match resolved
    # it, since covering your face at a security gate and then uncovering it
    # is itself a security-relevant moment. Also set True on the row's own
    # OCCLUSION_DETECTED status (redundant with status there, kept anyway so
    # a report can filter on this one field regardless of outcome).
    occlusion_detected = models.BooleanField(default=False)
    # Which dashboard account logged this row by hand - set ONLY on a
    # MANUAL_OVERRIDE row (a Security Officer's judgment call after visually
    # checking a physical ID when the scanner itself failed), never on an
    # automatic face/NFC match. This is what actually makes an override
    # "tied to their own account" rather than just labeled as one - without
    # this field, nothing on EntryLog identifies which staff member acted,
    # only which Person was verified. Null for every other row: there's no
    # dashboard account behind an automatic scan.
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="manual_override_logs",
    )

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
    scan saw a borderline/ambiguous face match, OR a confident match that's
    flagged as part of a confusable pair, and is waiting (up to
    TIEBREAK_TIMEOUT_SECONDS) for a card tap to confirm identity. At most one
    active tiebreak per gate."""

    class Reason(models.TextChoices):
        # The normal case: the score gap itself looked uncertain (see
        # IdentifyView._match_one_face's borderline/close_second checks).
        AMBIGUOUS_MATCH = "ambiguous_match", "Ambiguous match"
        # The score gap looked completely fine - the match was overridden
        # anyway because the matched person is on record as confusable with
        # someone else (see users.models.ConfusablePair). Runs independently
        # of, and takes priority over, the ambiguous-match check above.
        CONFUSABLE_PAIR = "confusable_pair", "Confusable pair"

    gate_location = models.CharField(max_length=100, unique=True)
    candidate_person_ids = models.JSONField()
    direction = models.CharField(max_length=10)
    reason = models.CharField(max_length=20, choices=Reason.choices, default=Reason.AMBIGUOUS_MATCH)
    created_at = models.DateTimeField(auto_now_add=True)


class OcclusionAttempt(models.Model):
    """One frame from the continuous scan whose mouth/nose read as covered
    (see IdentifyView._occlusion_prompt). A covered face is never logged as
    an EntryLog row - these only answer "was a covered face seen at this gate
    in the last few seconds" (IdentifyView._recent_occlusion_seen), which
    holds back an "Unknown" flickering in between covered frames and notes
    occlusion_detected on the entry that follows.

    No embedding stored, unlike Unmatched/SpoofAttempt: an occluded frame's
    embedding is exactly the thing insightface_utils.mouth_visibility_ratio's
    docstring says not to trust. Not a permanent audit record - old rows can
    be pruned freely, same as its siblings. (EntryLog.Status.
    OCCLUSION_DETECTED is kept only so rows logged before covered faces
    stopped being logged still display.)"""

    gate_location = models.CharField(max_length=100)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)


class SpoofAttempt(models.Model):
    """The liveness-check counterpart to UnmatchedAttempt: one face from the
    continuous scan that failed the passive liveness (anti-spoofing) check,
    before it was ever compared against enrolled embeddings. There's no
    confirmed Person to key voting on, so IdentifyView groups recent rows by
    embedding similarity the same way UnmatchedAttempt does, and only
    confirms a real EntryLog(status=SPOOF_SUSPECTED) once enough recent
    attempts agree within the same VOTE_REQUIRED_AGREEMENT/VOTE_WINDOW_SIZE/
    VOTE_WINDOW_SECONDS window a face match/non-match already votes with -
    so one oddly-lit or motion-blurred real frame can't alone flag someone as
    a spoof attempt. Not a permanent audit record - old rows can be pruned
    freely."""

    gate_location = models.CharField(max_length=100)
    embedding = models.JSONField()
    liveness_score = models.FloatField()
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
