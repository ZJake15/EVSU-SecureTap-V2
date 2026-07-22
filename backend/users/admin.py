from django.contrib import admin, messages

from .insightface_utils import compute_face_embedding, normalize_to_jpeg
from .models import FaceEmbedding, Person


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = (
        "full_name",
        "role",
        "student_or_employee_id",
        "nfc_id",
        "department_or_course",
        "is_active",
        "created_at",
    )
    list_filter = ("role", "is_active", "department_or_course")
    search_fields = ("full_name", "student_or_employee_id", "nfc_id")

    def save_model(self, request, obj, form, change):
        """The API's PersonSerializer computes a face embedding on save, but
        the admin form doesn't go through that - do the same thing here so a
        person added/edited via admin is still matchable by the camera scan.
        Adds a new embedding rather than replacing existing ones, same as
        the dashboard's edit flow - each photo upload is another enrollment
        shot, not a wholesale replacement."""
        super().save_model(request, obj, form, change)
        if not obj.photo_reference:
            return

        photo_changed = "photo_reference" in form.changed_data
        has_embedding = obj.face_embeddings.exists()
        if not photo_changed and has_embedding:
            return

        try:
            obj.photo_reference.open()
            embedding, det_score = compute_face_embedding(obj.photo_reference)
        except ValueError as exc:
            messages.warning(
                request,
                f"Saved, but no face embedding was computed: {exc} "
                "This person won't be matched by the camera scan until that's fixed.",
            )
            return

        obj.photo_reference.open()
        FaceEmbedding.objects.create(
            person=obj, embedding=embedding, source_image=obj.photo_reference, detection_score=det_score,
        )

        if photo_changed:
            # Browsers can't render some upload formats (HEIC, notably) -
            # store a normalized JPEG instead of whatever was uploaded.
            obj.photo_reference.open()
            obj.photo_reference = normalize_to_jpeg(obj.photo_reference)
            obj.save(update_fields=["photo_reference"])


@admin.register(FaceEmbedding)
class FaceEmbeddingAdmin(admin.ModelAdmin):
    list_display = ("person", "is_low_confidence", "detection_score", "created_at")
    list_filter = ("is_low_confidence",)
    search_fields = ("person__full_name",)
