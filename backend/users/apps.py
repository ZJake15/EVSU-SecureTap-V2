import os
import sys
from pathlib import Path

from django.apps import AppConfig
from django.conf import settings
from django.utils.autoreload import autoreload_started


def _watch_config_files(sender, **kwargs):
    """Makes `manage.py runserver` restart itself when backend/.env or the
    trained occlusion model changes, not just when a .py file does. Both are
    read only once, at startup - so without this, switching
    OCCLUSION_DETECTION_MODE (or retraining the model) silently did nothing
    until someone remembered to restart the backend by hand, and the launcher,
    which reuses a backend that's already running, never would."""
    for path in (Path(settings.ENV_FILE), Path(settings.OCCLUSION_CLASSIFIER_PATH)):
        sender.extra_files.add(path)


class UsersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "users"

    def ready(self):
        # Registers HEIC/HEIF support with Pillow - iPhones save photos in
        # this format by default, and without this, uploading one fails
        # Django's image validation before it even reaches face detection.
        import pillow_heif

        pillow_heif.register_heif_opener()
        autoreload_started.connect(_watch_config_files)

        # Photo files are deleted with (or replaced along with) the records
        # that point at them - see users/photo_files.py.
        from . import photo_files

        photo_files.connect()

        # The face models load on first use, which made the first scans
        # after starting slow - see users/warmup.py. Only for the web server
        # (in the process that serves - with auto-reload that's the child),
        # not for commands like migrate.
        if "runserver" in sys.argv and (os.environ.get("RUN_MAIN") == "true" or "--noreload" in sys.argv):
            from . import warmup

            warmup.start()
