"""The scheduled clean-up behind the Settings page's "Privacy & Data
Retention" section:

    python manage.py purge_old_data            # run it
    python manage.py purge_old_data --dry-run  # just report what it would delete

ALWAYS, whatever the Settings page says - none of this is anything a person
would look for, and keeping it only keeps personal data for no reason:
- photo files that no record points at any more (left behind by deletions
  before photo files were deleted together with their records),
- unknown face data older than UNKNOWN_FACE_HOURS: the face data saved with
  records of people who aren't registered (they never agreed to have their
  face stored; the scan needs it for at most a few hours, to recognize the
  same stranger again), plus the scan's short-lived working tables.

Only with "Automatic deletion" switched on, each by its own period:
- gate photos (the picture saved with an Unknown or suspected-fake record);
  the record itself stays,
- whole entry/exit records,
- audit log entries, only if a period is set (0 = keep forever).

What it NEVER touches: registered people (users.Person), their face data
(users.FaceEmbedding) or their registration photos.

There's no job scheduler in this project, so the system launcher
(launcher.py) runs this once when the backend comes up and then once a day
while it stays open. A server that runs without the launcher can use
Windows Task Scheduler instead - see documentation.md.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from audit.models import AuditLogEntry
from audit.utils import log_action
from configuration import store
from logs.models import EntryLog, OcclusionAttempt, RecognitionAttempt, SpoofAttempt, UnmatchedAttempt
from users import photo_files

# How long face data of people who aren't registered is kept. The longest
# anything uses it is the "Alert on repeated unknown faces" period (at most
# 4 hours); a day leaves room for that and nothing more.
UNKNOWN_FACE_HOURS = 24


class Command(BaseCommand):
    help = "Deletes old records using the Settings page's data-retention periods."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Only report what would be deleted.")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        now = timezone.now()
        counts = {}

        # ---- always --------------------------------------------------------
        leftover = photo_files.orphaned_media_files()
        counts["leftover_photo_files"] = len(leftover)
        if not dry_run:
            for path in leftover:
                path.unlink(missing_ok=True)

        unknown_cutoff = now - timezone.timedelta(hours=UNKNOWN_FACE_HOURS)
        face_rows = EntryLog.objects.filter(timestamp__lt=unknown_cutoff, unmatched_encoding__isnull=False)
        counts["unknown_face_data"] = face_rows.count()
        working_tables = (UnmatchedAttempt, SpoofAttempt, OcclusionAttempt, RecognitionAttempt)
        counts["scan_working_rows"] = sum(
            model.objects.filter(timestamp__lt=unknown_cutoff).count() for model in working_tables
        )
        if not dry_run:
            face_rows.update(unmatched_encoding=None)
            for model in working_tables:
                model.objects.filter(timestamp__lt=unknown_cutoff).delete()

        # ---- only with Automatic deletion on -------------------------------
        counts.update(gate_photos=0, entry_records=0, audit_entries=0)
        auto = store.get("auto_delete_enabled")
        if auto or dry_run:
            def cutoff(key):
                return now - timezone.timedelta(days=store.get(key))

            # Gate photos: delete the image file, keep the record.
            photo_rows = EntryLog.objects.filter(timestamp__lt=cutoff("keep_gate_photos_days")).exclude(
                captured_photo=""
            ).exclude(captured_photo__isnull=True)
            counts["gate_photos"] = photo_rows.count()
            if not dry_run:
                for log in photo_rows.iterator():
                    log.captured_photo.delete(save=False)
                    log.save(update_fields=["captured_photo"])

            # Whole entry/exit records - their photo files go with them (see
            # users/photo_files.py).
            old_records = EntryLog.objects.filter(timestamp__lt=cutoff("keep_entry_records_days"))
            counts["entry_records"] = old_records.count()
            if not dry_run:
                old_records.delete()

            # Audit log - only with a period set; 0 keeps it forever.
            audit_days = store.get("keep_audit_log_days")
            if audit_days:
                old_audit = AuditLogEntry.objects.filter(timestamp__lt=cutoff("keep_audit_log_days"))
                counts["audit_entries"] = old_audit.count()
                if not dry_run:
                    old_audit.delete()

        verb = "Would delete" if dry_run else "Deleted"
        line = (f"{verb}: {counts['leftover_photo_files']} leftover photo files, "
                f"{counts['unknown_face_data']} unknown-face records' face data, "
                f"{counts['scan_working_rows']} scan working rows")
        if auto or dry_run:
            line += (f", {counts['entry_records']} entry records, {counts['gate_photos']} gate photos, "
                     f"{counts['audit_entries']} audit entries")
            if dry_run and not auto:
                line += " (these last three only once Automatic deletion is switched on)"
        else:
            line += ". Automatic deletion is off in Settings, so entry records, gate photos and the audit log were kept"
        self.stdout.write(line + ".")
        if not dry_run and any(counts.values()):
            log_action(None, "data_purged", target_description="Scheduled clean-up", detail=counts)
