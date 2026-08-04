from django.core.management.base import BaseCommand

from users.insightface_utils import compute_face_embedding
from users.models import FaceEmbedding


class Command(BaseCommand):
    help = (
        "Regenerates every FaceEmbedding that has a source_image, using whatever InsightFace "
        "model pack insightface_utils is currently configured for. Required any time that pack "
        "changes (e.g. buffalo_l -> buffalo_s) - embeddings from different models live in "
        "different vector spaces, so gate-scan matching silently returns nonsense (or nothing) "
        "against stale embeddings from the old model. Embeddings with no source_image (e.g. "
        "seed_dummy_data's random placeholder rows) are left untouched - there's no photo to "
        "regenerate them from."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true", help="Report what would change without saving anything."
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        embeddings = FaceEmbedding.objects.exclude(source_image="").select_related("person")

        updated, failed = 0, 0
        for face_embedding in embeddings:
            label = f"{face_embedding.person.full_name} ({face_embedding.person.student_or_employee_id})"
            try:
                with face_embedding.source_image.open("rb") as fh:
                    embedding, det_score = compute_face_embedding(fh)
            except (ValueError, OSError) as exc:
                failed += 1
                self.stdout.write(self.style.ERROR(f"  FAILED  {label}: {exc}"))
                continue

            if not dry_run:
                face_embedding.embedding = embedding
                face_embedding.detection_score = det_score
                face_embedding.save(update_fields=["embedding", "detection_score"])
            updated += 1
            self.stdout.write(f"  {'would update' if dry_run else 'updated'}  {label} (det_score={det_score:.3f})")

        self.stdout.write(
            self.style.SUCCESS(f"\n{updated} embedding(s) {'would be ' if dry_run else ''}updated, {failed} failed.")
        )
        if failed:
            self.stdout.write(
                self.style.WARNING(
                    "Failed rows still hold their OLD embedding, from the previous model - re-enroll "
                    "those people's photos manually (the stored photo no longer passes the quality gate "
                    "under the new model)."
                )
            )
        if updated and not dry_run:
            self.stdout.write("Restart the Django server so it picks up the new model in its running process.")
