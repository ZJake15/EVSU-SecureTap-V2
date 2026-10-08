"""Copies everything from the MySQL database into a new SQLite file.

    python manage.py copy_mysql_to_sqlite            # create backend/db.sqlite3
    python manage.py copy_mysql_to_sqlite --replace  # redo it (old file kept as .bak)

Reads the MySQL connection from backend/.env (DB_NAME, DB_USER, ...) and
writes the SQLite file named by DB_PATH. MySQL is only read, never changed -
it stays as a backup. Afterwards, set DB_ENGINE=sqlite in backend/.env and
restart the backend. What is and isn't copied: see configuration/datacopy.py.
To bring data over from a different SecureTap folder, use import_securetap.
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from configuration import datacopy


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
            if settings.DATABASES["default"]["ENGINE"].endswith("sqlite3"):
                connections["default"].close()
            backup = datacopy.move_aside(target_path)
            self.stdout.write(f"Previous SQLite file kept as {backup.name}")

        datacopy.register(settings.MYSQL_DATABASE, settings.SQLITE_DATABASE)
        try:
            connections[datacopy.SOURCE].ensure_connection()
        except Exception as exc:
            raise CommandError(f"Couldn't connect to MySQL with the DB_* settings in backend/.env: {exc}")

        try:
            rows, mismatches = datacopy.copy_all(self.stdout.write)
        finally:
            datacopy.close()
        self.stdout.write("Checking every table arrived complete:")
        for label, before, after in rows:
            status = "ok" if before == after else "MISMATCH"
            self.stdout.write(f"  {label:32} MySQL {before:6}   SQLite {after:6}   {status}")
        if mismatches:
            raise CommandError(f"{mismatches} table(s) didn't copy completely - the SQLite file shouldn't be used.")
        self.stdout.write(self.style.SUCCESS(
            f"Done: {target_path}. Set DB_ENGINE=sqlite in backend/.env and restart the backend. "
            "MySQL wasn't changed and stays as a backup."
        ))
