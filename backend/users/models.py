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
