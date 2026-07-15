from django.contrib import admin, messages

from .face_utils import compute_face_encoding, normalize_to_jpeg
from .models import FaceEncoding, Person


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
        """The API's PersonSerializer computes a face encoding on save, but
        the admin form doesn't go through that - do the same thing here so a
        person added/edited via admin is still matchable by the camera scan."""
        super().save_model(request, obj, form, change)
        if not obj.photo_reference:
            return

        photo_changed = "photo_reference" in form.changed_data
        has_encoding = FaceEncoding.objects.filter(person=obj).exists()
        if not photo_changed and has_encoding:
            return

        try:
            obj.photo_reference.open()
            encoding = compute_face_encoding(obj.photo_reference)
        except ValueError as exc:
            messages.warning(
                request,
                f"Saved, but no face encoding was computed: {exc} "
                "This person won't be matched by the camera scan until that's fixed.",
            )
            return

        FaceEncoding.objects.update_or_create(person=obj, defaults={"encoding": encoding.tolist()})

        if photo_changed:
            # Browsers can't render some upload formats (HEIC, notably) -
            # store a normalized JPEG instead of whatever was uploaded.
            obj.photo_reference.open()
            obj.photo_reference = normalize_to_jpeg(obj.photo_reference)
            obj.save(update_fields=["photo_reference"])


@admin.register(FaceEncoding)
class FaceEncodingAdmin(admin.ModelAdmin):
    list_display = ("person", "created_at")
    search_fields = ("person__full_name",)
