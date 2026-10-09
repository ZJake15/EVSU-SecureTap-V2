"""Unlocks a backup file into a folder, for setup to bring its data in - it
runs import_securetap on that folder next, the same as for an old copy.

    python manage.py open_backup "E:\\SecureTap backup ....securetap-backup" "C:\\...\\empty folder"
    (the password: first line of input)

The folder holds the photos and the database unlocked, so the caller makes
it in this computer's temporary folder and deletes it when the import is
done. The last line printed is SECURETAP_RESULT followed by the backup's
manifest (when it was made, and how many people and records it holds).
"""

import json
import sys
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from configuration import backup_file

RESULT_MARKER = "SECURETAP_RESULT"


class Command(BaseCommand):
    help = "Unlocks a SecureTap backup file into an empty folder."

    def add_arguments(self, parser):
        parser.add_argument("file", help="The .securetap-backup file.")
        parser.add_argument("folder", help="An empty folder to unlock it into.")

    def handle(self, *args, **options):
        source, target = Path(options["file"]), Path(options["folder"])
        if not source.is_file():
            raise CommandError(f"{source} doesn't exist (is the USB drive still plugged in?).")
        if not target.is_dir() or any(target.iterdir()):
            raise CommandError(f"{target} has to be an empty folder.")
        password = sys.stdin.buffer.readline().decode("utf-8-sig").rstrip("\r\n")
        self.stdout.write(f"Unlocking {source.name}...")
        try:
            manifest = backup_file.read_backup(source, password, target)
        except backup_file.BackupError as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(f"  made {manifest.get('created_at', '?')} on {manifest.get('computer') or 'a computer'}: "
                          f"{manifest.get('people', 0)} people, {manifest.get('entry_logs', 0)} entry records")
        self.stdout.write(f"{RESULT_MARKER} {json.dumps(manifest)}")
