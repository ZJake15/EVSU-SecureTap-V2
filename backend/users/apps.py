from django.apps import AppConfig


class UsersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "users"

    def ready(self):
        # Registers HEIC/HEIF support with Pillow - iPhones save photos in
        # this format by default, and without this, uploading one fails
        # Django's image validation before it even reaches face detection.
        import pillow_heif

        pillow_heif.register_heif_opener()
