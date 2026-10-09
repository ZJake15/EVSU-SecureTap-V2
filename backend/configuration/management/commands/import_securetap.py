"""Brings everything from another SecureTap copy on this computer into this one.

    python manage.py import_securetap "C:\\path\\to\\the\\old\\copy"
    python manage.py import_securetap "C:\\path\\to\\the\\old\\copy" --replace

Point it at the old copy's folder (the one holding backend\\, dashboard\\,
entry-agent\\), or at a backup unlocked by manage.py open_backup. It reads that copy's database settings from its own
backend\\.env - MySQL or SQLite - and copies into this copy's SQLite
database:
  - the database: people and their faces, entry records, accounts, settings,
    the audit log (details in configuration/datacopy.py)
  - the photos (backend\\media)
  - the trained covered-face model, if the old copy has one and this doesn't
then checks every table's row count, and that stored faces still match
their photos. The old copy is only read, never changed.

This copy must not have data yet, or pass --replace (the current database is
kept as db.sqlite3.bak). The gate's own settings (gate name, camera, card
reader) live outside the backend - the launcher's setup brings those over.

The last line printed is SECURETAP_RESULT followed by a JSON summary, for
the launcher's setup window.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connections
from dotenv import dotenv_values

from configuration import backup_file, datacopy

RESULT_MARKER = "SECURETAP_RESULT"
# Re-computing a stored photo's face fingerprint should give the stored one
# back almost exactly when both copies use the same face model.
SAME_FACE_MODEL_SIMILARITY = 0.98


def _source_database(old_backend):
    """(engine, Django database settings) for the old copy, read from its
    backend/.env the same way settings.py reads this copy's."""
    # utf-8-sig: some Windows editors start the file with an invisible
    # byte-order mark, which would otherwise hide the first setting.
    env_path = old_backend / ".env"
    values = dotenv_values(env_path, encoding="utf-8-sig") if env_path.exists() else {}
    engine = (values.get("DB_ENGINE") or "").strip().lower() or ("mysql" if values.get("DB_NAME") else "sqlite")
    if engine == "mysql":
        if importlib.util.find_spec("MySQLdb") is None:
            raise CommandError("The old copy uses MySQL, and reading it needs MySQL support: "
                               "pip install -r requirements-mysql.txt")
        return engine, {
            **settings.MYSQL_DATABASE,
            "NAME": values.get("DB_NAME") or "securetap",
            "USER": values.get("DB_USER") or "securetap_app",
            "PASSWORD": values.get("DB_PASSWORD") or "",
            "HOST": values.get("DB_HOST") or "127.0.0.1",
            "PORT": values.get("DB_PORT") or "3306",
        }
    if engine != "sqlite":
        raise CommandError(f"The old copy's DB_ENGINE is {engine!r} - expected sqlite or mysql.")
    path = Path(values.get("DB_PATH") or old_backend / "db.sqlite3")
    if not path.exists():
        raise CommandError(f"The old copy has no database at {path}.")
    return engine, {**settings.SQLITE_DATABASE, "NAME": str(path)}


def _existing_data():
    """What this copy already holds - all zero for a database that hasn't
    even been created yet."""
    counts = {}
    for key, label in (("people", "users.Person"), ("entry_logs", "logs.EntryLog"),
                       ("accounts", "accounts.AdminProfile")):
        try:
            counts[key] = apps.get_model(label).objects.count()
        except DatabaseError:
            counts[key] = 0
    connections["default"].close()
    return counts


def _copy_tree(source, target):
    """Copies every file under source into target, skipping files already
    there with the same size. Returns (copied, skipped)."""
    copied = skipped = 0
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        destination = target / path.relative_to(source)
        if destination.exists() and destination.stat().st_size == path.stat().st_size:
            skipped += 1
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        copied += 1
    return copied, skipped


def _face_check(limit=5):
    """Re-computes a few stored photos' face fingerprints and compares them
    with the stored ones. (photos compared, lowest similarity)."""
    from users import insightface_utils
    from users.models import FaceEmbedding

    similarities = []
    for stored in FaceEmbedding.objects.exclude(source_image="").exclude(source_image__isnull=True)[:limit * 3]:
        if len(similarities) >= limit:
            break
        try:
            with stored.source_image.open("rb") as fh:
                fresh, _score = insightface_utils.compute_face_embedding(fh)
        except (OSError, ValueError):
            continue  # photo missing, or it no longer passes enrollment's quality bar
        similarities.append(float(np.dot(np.asarray(stored.embedding, dtype=np.float32),
                                         np.asarray(fresh, dtype=np.float32))))
    return len(similarities), (min(similarities) if similarities else None)


class Command(BaseCommand):
    help = "Brings the database, photos and covered-face model of another SecureTap copy into this one."

    def add_arguments(self, parser):
        parser.add_argument("folder", help="The old copy's folder (the one holding backend\\).")
        parser.add_argument("--replace", action="store_true",
                            help="Replace this copy's data (kept as db.sqlite3.bak).")
        parser.add_argument("--skip-face-check", action="store_true",
                            help="Don't re-check stored faces against their photos afterwards.")

    def handle(self, *args, **options):
        write = self.stdout.write
        old_root = Path(options["folder"]).expanduser().resolve()
        old_backend = old_root / "backend"
        # An unlocked backup (manage.py open_backup) is laid out like a copy,
        # just without the program files.
        if not (old_backend / "manage.py").exists() and not backup_file.is_unlocked_backup(old_root):
            raise CommandError(f"{old_root} isn't a SecureTap folder - it has no backend\\manage.py.")
        if old_backend == Path(settings.BASE_DIR).resolve():
            raise CommandError("That's this copy's own folder - choose the other copy's folder.")
        if not settings.DATABASES["default"]["ENGINE"].endswith("sqlite3"):
            raise CommandError("This copy uses MySQL. The import goes into SQLite - set DB_ENGINE=sqlite first.")
        target_path = Path(settings.DATABASES["default"]["NAME"]).resolve()

        engine, source = _source_database(old_backend)
        if engine == "sqlite" and Path(source["NAME"]).resolve() == target_path:
            raise CommandError("The old copy uses this copy's own database file - there's nothing to import.")
        write(f"Reading the old copy at {old_root} ({'MySQL' if engine == 'mysql' else 'SQLite'} database)...")

        existing = _existing_data()
        if any(existing.values()) and not options["replace"]:
            raise CommandError(
                f"This copy already has data ({existing['people']} people, {existing['entry_logs']} entry "
                f"records, {existing['accounts']} accounts). Use --replace to swap it for the old copy's "
                f"(the current database is kept as {target_path.name}.bak)."
            )

        temp_dir = None
        backup = None
        try:
            if engine == "sqlite":
                # Read a copy, never the old file itself: just opening an
                # SQLite file can make it finish a half-written change, and
                # an older one may need updating (below). The -wal/-shm files
                # hold changes not yet written into the main file.
                temp_dir = Path(tempfile.mkdtemp(prefix="securetap_import_"))
                temp_db = temp_dir / "old.sqlite3"
                for suffix in ("", "-wal", "-shm"):
                    part = Path(source["NAME"] + suffix)
                    if part.exists():
                        shutil.copy2(part, Path(str(temp_db) + suffix))
                source = {**source, "NAME": str(temp_db)}
            datacopy.register(source, settings.DATABASES["default"])
            try:
                connections[datacopy.SOURCE].ensure_connection()
            except Exception as exc:
                hint = " Is the MySQL service running?" if engine == "mysql" else ""
                raise CommandError(f"Couldn't open the old copy's database.{hint} ({exc})")

            missing, extra = datacopy.migration_differences(datacopy.SOURCE)
            if extra:
                raise CommandError("The old copy is newer than this one (its database has changes this copy "
                                   "doesn't know about). Update this copy first, then import again.")
            if missing and engine == "mysql":
                raise CommandError(
                    "The old copy is older than this one, so its database needs updating first. Update that "
                    "copy (git pull, then python manage.py migrate in its backend folder), then import again."
                )
            if missing:
                # SQLite: SOURCE is already the temporary copy (above), so
                # updating it leaves the old copy itself untouched. Run as its
                # own `manage.py migrate` with the copy as ITS main database:
                # some migrations' data steps always write through the main
                # connection, and here that would be this copy's instead.
                write(f"The old copy is older - updating a temporary copy of its database ({len(missing)} "
                      "change(s))...")
                datacopy.close()
                update = subprocess.run(
                    [sys.executable, str(Path(settings.BASE_DIR) / "manage.py"), "migrate", "--verbosity", "0"],
                    env={**os.environ, "DB_ENGINE": "sqlite", "DB_PATH": source["NAME"]},
                    capture_output=True, text=True,
                )
                if update.returncode != 0:
                    raise CommandError("Couldn't update the old copy's database: "
                                       + (update.stderr.strip().splitlines() or ["unknown error"])[-1])

            datacopy.close()
            connections["default"].close()
            backup = datacopy.move_aside(target_path)
            if backup and any(existing.values()):
                write(f"This copy's previous database is kept as {backup.name}")
            datacopy.register(source, settings.DATABASES["default"])
            try:
                rows, mismatches = datacopy.copy_all(write)
            except Exception:
                self._undo(target_path, backup)
                raise
            if mismatches:
                self._undo(target_path, backup)
                raise CommandError(f"{mismatches} table(s) didn't copy completely - nothing was changed.")
            write("Every table arrived complete:")
            for label, before, after in rows:
                write(f"  {label:32} old {before:6}   new {after:6}   ok")
        finally:
            datacopy.close()
            if temp_dir is not None:
                shutil.rmtree(temp_dir, ignore_errors=True)

        write("Copying the photos...")
        old_media = old_backend / "media"
        copied, skipped = _copy_tree(old_media, Path(settings.MEDIA_ROOT)) if old_media.is_dir() else (0, 0)
        write(f"  {copied} photo file(s) copied" + (f", {skipped} already here" if skipped else ""))

        model_copied = False
        old_model = old_backend / "users" / Path(settings.OCCLUSION_CLASSIFIER_PATH).name
        new_model = Path(settings.OCCLUSION_CLASSIFIER_PATH)
        if old_model.exists() and not new_model.exists():
            new_model.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(old_model, new_model)
            model_copied = True
            write("  The trained covered-face model was copied too.")

        compared, lowest = (0, None)
        if not options["skip_face_check"]:
            write("Checking that stored faces still match their photos...")
            compared, lowest = _face_check()
            if lowest is None:
                write("  No usable photo to check with - skipped.")
            elif lowest >= SAME_FACE_MODEL_SIMILARITY:
                write(f"  ok ({compared} checked, similarity {lowest:.3f} or better)")
            else:
                write(self.style.WARNING(
                    f"  The faces were made with a different face model (similarity {lowest:.3f}). Run "
                    "python manage.py recompute_embeddings so people are recognized."
                ))

        counts = {label: after for label, _before, after in rows}
        profiles = apps.get_model("accounts.AdminProfile").objects
        result = {
            "people": counts.get("users.Person", 0),
            "faces": counts.get("users.FaceEmbedding", 0),
            "entry_logs": counts.get("logs.EntryLog", 0),
            "accounts": counts.get("accounts.AdminProfile", 0),
            "active_admins": profiles.filter(role="admin", user__is_active=True).count(),
            "photos_copied": copied,
            "model_copied": model_copied,
            "faces_checked": compared,
            "lowest_similarity": None if lowest is None else round(lowest, 4),
            "faces_need_recompute": lowest is not None and lowest < SAME_FACE_MODEL_SIMILARITY,
            "backup": str(backup) if backup and any(existing.values()) else None,
        }
        write(self.style.SUCCESS(
            f"Done: {result['people']} people, {result['faces']} face photos' data, {result['entry_logs']} entry "
            f"records and {result['accounts']} accounts are now in this copy. The old copy wasn't changed."
        ))
        write(f"{RESULT_MARKER} {json.dumps(result)}")

    @staticmethod
    def _undo(target_path, backup):
        """Throws away a half-finished copy and puts the previous database
        back, so a failed import leaves this copy as it was."""
        connections["default"].close()
        datacopy.close()
        for suffix in ("", "-wal", "-shm"):
            Path(str(target_path) + suffix).unlink(missing_ok=True)
        if backup:
            for suffix in ("", "-wal", "-shm"):
                saved = Path(str(backup) + suffix)
                if saved.exists():
                    saved.replace(Path(str(target_path) + suffix))
