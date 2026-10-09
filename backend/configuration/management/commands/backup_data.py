"""Saves everything into one password-locked backup file - the launcher's
"Back up data" button.

    python manage.py backup_data "E:\\"      (the password: first line of input)

Writes "SecureTap backup <date> <time>.securetap-backup" into that folder -
see configuration/backup_file.py for what's inside and how it's locked. The
database is copied with SQLite's own backup, so the copy is complete and
consistent even while the backend runs and the gate keeps scanning. The
file only gets its real name once it's complete, so a cancelled or failed
backup never leaves a file that looks usable. Each backup is recorded in the
Audit Log.

To bring a backup back: setup ("Run setup again" in the launcher) > Your
data > choose the backup file (manage.py open_backup, then import_securetap).

The last line printed is SECURETAP_RESULT followed by a JSON summary, for
the launcher.
"""

import json
import platform
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from accounts.models import AdminProfile
from audit.utils import log_action
from configuration import backup_file
from logs.models import EntryLog
from users.models import FaceEmbedding, Person

RESULT_MARKER = "SECURETAP_RESULT"
# Photos are already compressed - zipping them again only costs time.
_STORED = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".joblib")


def _gate_settings():
    """(entry-agent/.env gate lines, launcher choices) - the same pieces an
    import brings over from an old copy (device_setup.import_gate_settings),
    read through device_setup so they come from wherever this copy keeps
    them. Never the gate key or any other secret."""
    sys.path.insert(0, str(Path(settings.BASE_DIR).parent))
    try:
        import device_setup
    except ImportError:
        return {}, {}
    env = device_setup._read_env(device_setup.ENTRY_AGENT_ENV)
    gate = {key: env[key] for key in device_setup.GATE_ENV_KEYS if (env.get(key) or "").strip()}
    try:
        saved = json.loads(device_setup.LAUNCHER_SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    return gate, {key: saved[key] for key in device_setup.LAUNCHER_KEYS if key in saved}


def _free_name(folder, stamp):
    name = f"SecureTap backup {stamp}"
    candidate = folder / f"{name}{backup_file.SUFFIX}"
    number = 2
    while candidate.exists():
        candidate = folder / f"{name} ({number}){backup_file.SUFFIX}"
        number += 1
    return candidate


class Command(BaseCommand):
    help = "Saves the database, photos and gate settings into one password-locked backup file."

    def add_arguments(self, parser):
        parser.add_argument("folder", help="Where to save it - a USB drive, for example.")

    def handle(self, *args, **options):
        if not settings.DATABASES["default"]["ENGINE"].endswith("sqlite3"):
            raise CommandError("Backups are made from the SQLite database - this copy uses MySQL.")
        folder = Path(options["folder"]).expanduser()
        if not folder.is_dir():
            raise CommandError(f"{folder} isn't a folder (is the USB drive still plugged in?).")
        # utf-8-sig, as create_first_admin: drops the invisible byte-order
        # mark some Windows shells put in front of piped text.
        password = sys.stdin.buffer.readline().decode("utf-8-sig").rstrip("\r\n")
        if len(password) < backup_file.MIN_PASSWORD_LENGTH:
            raise CommandError(f"The backup password needs at least {backup_file.MIN_PASSWORD_LENGTH} characters.")

        media_root = Path(settings.MEDIA_ROOT)
        photos = sorted(path for path in media_root.rglob("*") if path.is_file()) if media_root.is_dir() else []
        classifier = Path(settings.OCCLUSION_CLASSIFIER_PATH)
        gate, launcher = _gate_settings()
        now = timezone.localtime()
        summary = {
            "people": Person.objects.count(),
            "faces": FaceEmbedding.objects.count(),
            "entry_logs": EntryLog.objects.count(),
            "accounts": AdminProfile.objects.count(),
            "photos": len(photos),
        }
        manifest = {"format": backup_file.VERSION, "created_at": now.isoformat(timespec="seconds"),
                    "computer": platform.node(), **summary}
        target = _free_name(folder, now.strftime("%Y-%m-%d %H%M"))
        partial = target.with_name(target.name + ".partial")
        self.stdout.write(f"Backing up {summary['people']} people, {summary['entry_logs']} entry records and "
                          f"{len(photos)} photo files to {target}...")

        with tempfile.TemporaryDirectory(prefix="securetap_backup_") as temp:
            snapshot = Path(temp) / "db.sqlite3"
            source = sqlite3.connect(settings.DATABASES["default"]["NAME"])
            copy = sqlite3.connect(snapshot)
            try:
                source.backup(copy)
            finally:
                copy.close()
                source.close()

            def add_contents(archive):
                archive.writestr(backup_file.MANIFEST, json.dumps(manifest, indent=2))
                archive.write(snapshot, "backend/db.sqlite3")
                for photo in photos:
                    archive.write(photo, "backend/media/" + photo.relative_to(media_root).as_posix(),
                                  compress_type=zipfile.ZIP_STORED if photo.suffix.lower() in _STORED else None)
                if classifier.is_file():
                    archive.write(classifier, "backend/users/" + classifier.name, compress_type=zipfile.ZIP_STORED)
                archive.writestr("backend/.env", f"TIME_ZONE={settings.TIME_ZONE}\n")
                if gate:
                    archive.writestr("entry-agent/.env", "".join(f"{key}={value}\n" for key, value in gate.items()))
                if launcher:
                    archive.writestr("launcher_settings.json", json.dumps(launcher, indent=2))

            try:
                backup_file.write_backup(partial, password, add_contents)
                partial.replace(target)
            except BaseException:
                partial.unlink(missing_ok=True)
                raise

        size_mb = round(target.stat().st_size / 1024 ** 2, 1)
        log_action(None, "data_backed_up", target_description=str(target), detail={**summary, "size_mb": size_mb})
        self.stdout.write(self.style.SUCCESS(f"Done: {target} ({size_mb} MB)."))
        self.stdout.write(f"{RESULT_MARKER} {json.dumps({'file': str(target), 'size_mb': size_mb, **summary})}")
