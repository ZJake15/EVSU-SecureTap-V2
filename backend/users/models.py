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


class FaceEncoding(models.Model):
    """The 128-d face_recognition encoding vector for a Person - not the raw photo."""

    person = models.OneToOneField(Person, on_delete=models.CASCADE, related_name="face_encoding")
    encoding = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Encoding for {self.person.full_name}"
