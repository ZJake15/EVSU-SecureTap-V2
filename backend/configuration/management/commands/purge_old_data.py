"""The scheduled clean-up behind the Settings page's "Privacy & Data
Retention" section:

    python manage.py purge_old_data            # delete (only if Automatic deletion is on)
    python manage.py purge_old_data --dry-run  # just report what it would delete

What it deletes, each by its own period from the Settings page:
- gate photos (the picture saved with an Unknown or suspected-fake record);
  the record itself stays,
- unknown face data: the face data saved with records of people who aren't
  registered, plus the scan's short-lived working tables,
- whole entry/exit records,
- audit log entries, only if a period is set (0 = keep forever).

What it NEVER touches: registered people (users.Person), their face data
(users.FaceEmbedding) or their registration photos. Those tables aren't
even imported here.

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


class Command(BaseCommand):
    help = "Deletes old records using the Settings page's data-retention periods."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Only report what would be deleted.")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        if not store.get("auto_delete_enabled") and not dry_run:
            self.stdout.write("Automatic deletion is off in Settings - nothing deleted.")
            return

        now = timezone.now()

        def cutoff(key):
            return now - timezone.timedelta(days=store.get(key))

        counts = {}

        # Gate photos: delete the image file, keep the record.
        photo_rows = EntryLog.objects.filter(timestamp__lt=cutoff("keep_gate_photos_days")).exclude(
            captured_photo=""
        ).exclude(captured_photo__isnull=True)
        counts["gate_photos"] = photo_rows.count()
        if not dry_run:
            for log in photo_rows.iterator():
                log.captured_photo.delete(save=False)
                log.save(update_fields=["captured_photo"])

        # Unknown face data: the face data kept with Unknown/fake records
        # (registered people's records never carry any), and the scan's
        # working tables, which are only needed for a few seconds anyway.
        unknown_cutoff = cutoff("keep_unknown_face_days")
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

        # Whole entry/exit records (any photo file goes with them).
        old_records = EntryLog.objects.filter(timestamp__lt=cutoff("keep_entry_records_days"))
        counts["entry_records"] = old_records.count()
        if not dry_run:
            for log in old_records.exclude(captured_photo="").exclude(captured_photo__isnull=True).iterator():
                log.captured_photo.delete(save=False)
            old_records.delete()

        # Audit log - only with a period set; 0 keeps it forever.
        audit_days = store.get("keep_audit_log_days")
        counts["audit_entries"] = 0
        if audit_days:
            old_audit = AuditLogEntry.objects.filter(timestamp__lt=cutoff("keep_audit_log_days"))
            counts["audit_entries"] = old_audit.count()
            if not dry_run:
                old_audit.delete()

        verb = "Would delete" if dry_run else "Deleted"
        self.stdout.write(
            f"{verb}: {counts['entry_records']} entry records, {counts['gate_photos']} gate photos, "
            f"{counts['unknown_face_data']} unknown-face records' face data, "
            f"{counts['scan_working_rows']} scan working rows, {counts['audit_entries']} audit entries."
        )
        if not dry_run and any(counts.values()):
            log_action(None, "data_purged", target_description="Scheduled clean-up", detail=counts)
