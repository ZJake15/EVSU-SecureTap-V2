"""Copying a whole SecureTap database into another one - shared by
`copy_mysql_to_sqlite` and `import_securetap`.

Copied: people and their face data, entry records, accounts, settings, the
audit log - everything except Django's own lookup tables (rebuilt by
`migrate`), sign-in tokens (everyone just signs in again) and the gate
scan's short-lived working tables (only needed for a few seconds). Photos
aren't in the database at all - they live in backend/media/.
"""

import os
import tempfile
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.management import call_command
from django.db import connections
from django.db.migrations.loader import MigrationLoader

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


def register(source_settings, target_settings):
    """Adds the SOURCE and TARGET connections, configured the same way Django
    configures its normal ones."""
    configured = connections.configure_settings({
        "default": settings.DATABASES["default"],
        SOURCE: dict(source_settings),
        TARGET: dict(target_settings),
    })
    for alias in (SOURCE, TARGET):
        if alias in connections.settings:
            connections[alias].close()
            # Django keeps the connection object it made from the old
            # settings - without this, a second register() with a different
            # file would silently keep using the first one.
            try:
                del connections[alias]
            except AttributeError:
                pass  # never opened
        connections.settings[alias] = configured[alias]


def close():
    for alias in (SOURCE, TARGET, "default"):
        if alias in connections.settings:
            connections[alias].close()


def move_aside(path):
    """Renames an SQLite file (and its -wal/-shm companions) to .bak, so the
    copy starts from an empty file and the old data isn't lost. Returns the
    backup's path, or None if there was nothing to move."""
    path = Path(path)
    if not path.exists():
        return None
    backup = path.with_suffix(path.suffix + ".bak")
    for suffix in ("", "-wal", "-shm"):
        old = Path(str(path) + suffix)
        if old.exists():
            old.replace(Path(str(backup) + suffix))
    return backup


def local_app_labels():
    """This project's own apps (not Django's or third-party ones)."""
    base = Path(settings.BASE_DIR).resolve()
    return {
        config.label for config in apps.get_app_configs()
        if Path(config.path).resolve().is_relative_to(base)
    }


def migration_differences(alias):
    """(missing, extra): this project's migrations that the database at
    `alias` hasn't had applied, and ones it has that this code doesn't know
    (it came from a newer copy). Both empty means the two have exactly the
    same tables and columns, so the data can be copied as it is."""
    local = local_app_labels()
    on_disk = {key for key in MigrationLoader(None, ignore_no_migrations=True).disk_migrations if key[0] in local}
    with connections[alias].cursor() as cursor:
        cursor.execute("SELECT app, name FROM django_migrations")
        applied = {(app, name) for app, name in cursor.fetchall() if app in local}
    return sorted(on_disk - applied), sorted(applied - on_disk)


def target_alias():
    """TARGET - or "default" when TARGET is the backend's own database file.
    Two connections to one SQLite file lock each other out during `migrate`:
    some migrations' data steps always write through the default connection
    while TARGET holds its own write lock."""
    default, target = settings.DATABASES["default"], connections.settings[TARGET]
    same_file = (
        default["ENGINE"] == target["ENGINE"] and default["ENGINE"].endswith("sqlite3")
        and Path(default["NAME"]).resolve() == Path(target["NAME"]).resolve()
    )
    return "default" if same_file else TARGET


def copy_all(write):
    """Creates the target's tables, copies SOURCE's data into it and checks
    every table arrived complete. write(line) reports progress. Returns
    [(model label, rows in source, rows in target)] for every table with
    data, and the number of tables that didn't match."""
    target = target_alias()
    write("Creating the new database's tables...")
    call_command("migrate", database=target, verbosity=0)

    write("Copying the data...")
    handle, dump_path = tempfile.mkstemp(suffix=".json", prefix="securetap_copy_")
    os.close(handle)
    try:
        call_command(
            "dumpdata", database=SOURCE, natural_foreign=True, natural_primary=True,
            exclude=NOT_COPIED, output=dump_path, verbosity=0,
        )
        call_command("loaddata", dump_path, database=target, verbosity=0)
    finally:
        os.remove(dump_path)

    rows, mismatches = [], 0
    for model in apps.get_models():
        if not _is_copied(model):
            continue
        before = model._default_manager.using(SOURCE).count()
        after = model._default_manager.using(target).count()
        if before or after:
            rows.append((model._meta.label, before, after))
            mismatches += before != after
    return rows, mismatches
