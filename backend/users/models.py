from django.conf import settings
from django.db import models


class Person(models.Model):
    """A student or staff member enrolled in SecureTap (NFC + face verification)."""

    class Role(models.TextChoices):
        STUDENT = "student", "Student"
        STAFF = "staff", "Staff"

    full_name = models.CharField(max_length=255)
    role = models.CharField(max_length=20, choices=Role.choices)
    student_or_employee_id = models.CharField(max_length=50, unique=True)
    # unique=True already gives this an index for fast tap-time lookups.
    nfc_id = models.CharField(max_length=100, unique=True)
    department_or_course = models.CharField(max_length=255, blank=True)
    photo_reference = models.ImageField(upload_to="reference_photos/", null=True, blank=True)
    is_active = models.BooleanField(default=True)
    # A manual backstop for a confusable pair (see ConfusablePair below) - a
    # visible mole, scar, glasses, whatever a guard could actually check by
    # eye. Free text, entirely for a human reading it on a match/tiebreak
    # screen - never fed into face matching itself, which is exactly the
    # point: this is the thing the matcher structurally can't see, so it has
    # to reach a person some other way.
    distinguishing_note = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["full_name"]

    def __str__(self):
        return f"{self.full_name} ({self.student_or_employee_id})"


class FaceEmbedding(models.Model):
    """One 512-d ArcFace (InsightFace buffalo_s) embedding for a Person - a
    person can have several (a guided multi-photo enrollment captures 3-5
    with slight variation), not just one, so a gate-time match compares
    against every embedding a person has and takes their best score. The
    source photo is kept alongside the vector in case the model changes
    later and embeddings need to be regenerated from the original images."""

    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="face_embeddings")
    embedding = models.JSONField()
    # Nullable only for fabricated dummy/seed rows with no real photo behind
    # them (see seed_dummy_data) - every embedding created through the real
    # API/admin/bulk-import paths always has one.
    source_image = models.ImageField(upload_to="enrollment_photos/%Y/%m/%d/", null=True, blank=True)
    # InsightFace's own detection confidence for the face this embedding came
    # from - not the same thing as match confidence at gate time, just a
    # record of how confidently the enrollment photo's face was detected.
    detection_score = models.FloatField(null=True, blank=True)
    # True for embeddings that came from a single-photo path with no live
    # quality gate a human confirmed (bulk CSV/XLSX import) - lets the
    # dashboard/thesis evaluation distinguish "guided capture" enrollments
    # from "whatever photo happened to be on file" ones.
    is_low_confidence = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Embedding for {self.person.full_name} ({self.created_at:%Y-%m-%d})"


class DeactivationRequest(models.Model):
    """The maker-checker state for a SASO-initiated deactivation: SASO can
    enroll and edit a Person, but not deactivate one directly - this is the
    "request, not an immediate change" that stands in for that, which only an
    Admin can approve or reject (see users/views.py's DeactivationRequestViewSet).

    A dedicated table rather than a `pending_deactivation` flag + requester
    FK bolted onto Person, for two reasons worth defending to a panel: (1) it
    preserves full history - a request that gets rejected, and the same
    Person requested again later, is naturally just two rows here, where a
    single mutable flag on Person can only ever represent "is one pending
    right now", overwriting whatever came before; (2) it matches a pattern
    this codebase already uses for exactly this kind of short-lived workflow
    state (PendingTiebreak, in logs/models.py, is a different feature but the
    same shape: a row that exists only until it's resolved one way or the
    other).

    Not the same thing as an EntryLog or an AuditLogEntry - EntryLog is a gate
    event (someone was recognized here), AuditLogEntry is deliberately generic
    for "who changed what". A DeactivationRequest is only ever in play
    between being created and being resolved; it's the SOURCE of two
    AuditLogEntry rows (one for the request, one for the resolution), not a
    replacement for logging either."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="deactivation_requests")
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="deactivation_requests_made"
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    # Why SASO believes this person should be deactivated - shown to the
    # Admin reviewing it, not required to be exhaustive, but required to be
    # present: a bare "deactivate this person" request with no stated reason
    # gives the approving Admin nothing to actually evaluate.
    reason = models.CharField(max_length=500)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="deactivation_requests_resolved",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_note = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["-requested_at"]

    def __str__(self):
        return f"Deactivate {self.person.full_name} - {self.status} (requested {self.requested_at:%Y-%m-%d})"


class ConfusablePairManager(models.Manager):
    def get_or_create_pair(self, person_a, person_b, source, similarity=None, flagged_by=None):
        """(A, B) and (B, A) are the same real-world fact - stored under one
        canonical ordering (lower id first) so a duplicate flag from either
        direction always lands on the same row instead of silently creating
        a second, redundant one. Returns (pair, created), same shape as the
        underlying get_or_create."""
        first, second = (person_a, person_b) if person_a.id < person_b.id else (person_b, person_a)
        return self.get_or_create(
            person_a=first, person_b=second,
            defaults={"source": source, "detected_similarity": similarity, "flagged_by": flagged_by},
        )

    def partner_ids_for(self, person_id):
        """Every other Person id this one is flagged confusable with,
        regardless of which side of the pair they're stored on."""
        pairs = self.filter(models.Q(person_a_id=person_id) | models.Q(person_b_id=person_id))
        return [
            pair.person_b_id if pair.person_a_id == person_id else pair.person_a_id
            for pair in pairs
        ]


class ConfusablePair(models.Model):
    """A persistent flag that two specific Person records are unusually
    similar in face-embedding space - identical twins, siblings, or any two
    people no 2D face system can be expected to reliably tell apart. This is
    the thing that makes that fact keep mattering at every future gate scan,
    not just the moment it was first noticed: see IdentifyView, which checks
    this on every match before trusting a face-only confirmation (see
    settings.CONFUSABLE_SIMILARITY_THRESHOLD for the detection side, at
    enrollment).

    Not a `pending`/`dismissed` workflow like DeactivationRequest - once
    flagged, a pair stays in force. The tradeoff a false positive costs (one
    extra card tap at the gate) is deliberately far cheaper than what a false
    negative costs (a lookalike getting waved through on face alone), so
    there's no built-in way to turn the protection off short of deleting the
    record outright (see ConfusablePairViewSet.destroy - Admin/SASO can
    remove a pair they're confident was flagged in error)."""

    class Source(models.TextChoices):
        AUTO_DETECTED = "auto_detected", "Auto-detected at enrollment"
        MANUAL = "manual", "Manually flagged"

    person_a = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="confusable_as_a")
    person_b = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="confusable_as_b")
    source = models.CharField(max_length=20, choices=Source.choices)
    # The highest cross-person similarity actually observed when this was
    # auto-detected - null for a manually-flagged pair with no measurement
    # behind it (e.g. a guard reporting a real-world mix-up).
    detected_similarity = models.FloatField(null=True, blank=True)
    flagged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="confusable_pairs_flagged",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ConfusablePairManager()

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["person_a", "person_b"], name="unique_confusable_pair"),
        ]

    def __str__(self):
        return f"{self.person_a.full_name} <-> {self.person_b.full_name} ({self.source})"
