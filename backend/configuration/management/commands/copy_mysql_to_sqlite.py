"""Copies everything from the MySQL database into a new SQLite file.

    python manage.py copy_mysql_to_sqlite            # create backend/db.sqlite3
    python manage.py copy_mysql_to_sqlite --replace  # redo it (old file kept as .bak)

Reads the MySQL connection from backend/.env (DB_NAME, DB_USER, ...) and
writes the SQLite file named by DB_PATH. MySQL is only read, never changed -
it stays as a backup. Afterwards, set DB_ENGINE=sqlite in backend/.env and
restart the backend.

Copied: people and their face data, entry records, accounts, settings, the
audit log - everything except Django's own lookup tables (rebuilt by
`migrate`), sign-in tokens (everyone just signs in again) and the gate
scan's short-lived working tables (only needed for a few seconds).
Photos aren't in the database at all - they stay in backend/media/.
"""

import os
import tempfile
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connections

SOURCE, TARGET = "copy_source", "copy_target"

NOT_COPIED = [
    "contenttypes",
    "auth.permission",
    "sessions",
    "token_blacklist",
    "logs.RecognitionAttempt",
    "logs.UnmatchedAttempt",
    "logs.SpoofAttempt",
    "logs.OcclusionAttempt",
    "logs.PendingTiebreak",
]


def _is_copied(model):
    label = model._meta.label.lower()
    return model._meta.managed and not model._meta.proxy and not any(
        label == skip.lower() or label.split(".")[0] == skip.lower() for skip in NOT_COPIED
    )


class Command(BaseCommand):
    help = "Copies the MySQL database into a new SQLite file (MySQL is left untouched)."

    def add_arguments(self, parser):
        parser.add_argument("--replace", action="store_true",
                            help="Replace an existing SQLite file (it's renamed to .bak first).")

    def handle(self, *args, **options):
        target_path = Path(settings.SQLITE_DATABASE["NAME"])
        if target_path.exists():
            if not options["replace"]:
                raise CommandError(f"{target_path} already exists. Use --replace to redo the copy "
                                   "(the existing file is kept as a .bak).")
            backup = target_path.with_suffix(target_path.suffix + ".bak")
            for suffix in ("", "-wal", "-shm"):
                old = Path(str(target_path) + suffix)
                if old.exists():
                    old.replace(Path(str(backup) + suffix))
            self.stdout.write(f"Previous SQLite file kept as {backup.name}")

        # Two extra connections just for this command, configured the same
        # way Django configures its normal ones.
        configured = connections.configure_settings({
            "default": settings.DATABASES["default"],
            SOURCE: dict(settings.MYSQL_DATABASE),
            TARGET: dict(settings.SQLITE_DATABASE),
        })
        connections.settings[SOURCE] = configured[SOURCE]
        connections.settings[TARGET] = configured[TARGET]
        try:
            connections[SOURCE].ensure_connection()
        except Exception as exc:
            raise CommandError(f"Couldn't connect to MySQL with the DB_* settings in backend/.env: {exc}")

        self.stdout.write("Creating the SQLite database's tables...")
        call_command("migrate", database=TARGET, verbosity=0)

        self.stdout.write("Copying data from MySQL...")
        handle, dump_path = tempfile.mkstemp(suffix=".json", prefix="securetap_copy_")
        os.close(handle)
        try:
            call_command(
                "dumpdata", database=SOURCE, natural_foreign=True, natural_primary=True,
                exclude=NOT_COPIED, output=dump_path, verbosity=0,
            )
            call_command("loaddata", dump_path, database=TARGET, verbosity=0)
        finally:
            os.remove(dump_path)

        self.stdout.write("Checking every table arrived complete:")
        mismatches = 0
        for model in apps.get_models():
            if not _is_copied(model):
                continue
            before = model._default_manager.using(SOURCE).count()
            after = model._default_manager.using(TARGET).count()
            if before or after:
                status = "ok" if before == after else "MISMATCH"
                mismatches += status != "ok"
                self.stdout.write(f"  {model._meta.label:32} MySQL {before:6}   SQLite {after:6}   {status}")
        connections[SOURCE].close()
        connections[TARGET].close()
        if mismatches:
            raise CommandError(f"{mismatches} table(s) didn't copy completely - the SQLite file shouldn't be used.")
        self.stdout.write(self.style.SUCCESS(
            f"Done: {target_path}. Set DB_ENGINE=sqlite in backend/.env and restart the backend. "
            "MySQL wasn't changed and stays as a backup."
        ))
