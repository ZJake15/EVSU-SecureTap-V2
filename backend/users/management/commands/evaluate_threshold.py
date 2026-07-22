import glob
import os

import numpy as np
from django.core.management.base import BaseCommand, CommandError

from users.insightface_utils import compute_face_embedding
from users.models import Person
from users.threshold_eval import DEFAULT_THRESHOLD_SWEEP, far_frr_table, leave_one_out_similarities

# Sweep range for the printed table - the actual FACE_MATCH_SIMILARITY_THRESHOLD
# setting should end up wherever the false-accept-rate column first reaches
# an acceptable value for the thesis's stated security requirement.
THRESHOLD_SWEEP = DEFAULT_THRESHOLD_SWEEP


class Command(BaseCommand):
    help = (
        "Prints a false-accept-rate / false-reject-rate table across a range of "
        "candidate similarity thresholds, to pick and justify FACE_MATCH_SIMILARITY_THRESHOLD. "
        "Default mode is leave-one-out over whatever's currently enrolled - every embedding is "
        "compared against every other person's embeddings (false-accept signal) and every other "
        "embedding of the same person (false-reject signal). Pass --test-dir for a more rigorous "
        "held-out evaluation using fresh photos that were never used for enrollment."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--test-dir",
            help=(
                "Folder of fresh, non-enrollment photos named <student_or_employee_id>_*.jpg "
                "(e.g. 2022-00789_1.jpg, 2022-00789_2.jpg) - each photo is compared against its "
                "own person's enrolled embeddings (false-reject signal) and every other enrolled "
                "person's embeddings (false-accept signal), instead of the leave-one-out default."
            ),
        )

    def handle(self, *args, **options):
        test_dir = options.get("test_dir")
        if test_dir:
            same_person_similarities, cross_person_similarities = self._evaluate_test_dir(test_dir)
        else:
            same_person_similarities, cross_person_similarities = self._evaluate_leave_one_out()

        if not same_person_similarities and not cross_person_similarities:
            raise CommandError(
                "Nothing to evaluate - no comparable embeddings found. Enroll at least two people "
                "with real photos (or two photos of the same person) first."
            )

        self.stdout.write(
            f"\n{len(same_person_similarities)} same-person comparisons, "
            f"{len(cross_person_similarities)} cross-person comparisons\n"
        )
        self._print_table(same_person_similarities, cross_person_similarities)

    # ---- leave-one-out over current enrollment -----------------------------

    def _evaluate_leave_one_out(self):
        return leave_one_out_similarities()

    # ---- held-out test photos ----------------------------------------------

    def _evaluate_test_dir(self, test_dir):
        if not os.path.isdir(test_dir):
            raise CommandError(f"--test-dir '{test_dir}' is not a directory.")

        people = list(Person.objects.filter(is_active=True).prefetch_related("face_embeddings"))
        enrolled_by_id = {person.student_or_employee_id: person for person in people}

        same_person, cross_person = [], []
        for path in sorted(glob.glob(os.path.join(test_dir, "*"))):
            filename = os.path.basename(path)
            student_id = filename.split("_")[0]
            owner = enrolled_by_id.get(student_id)
            if owner is None:
                self.stdout.write(self.style.WARNING(f"  skipping {filename}: no enrolled person '{student_id}'"))
                continue

            try:
                with open(path, "rb") as fh:
                    embedding, _det_score = compute_face_embedding(fh)
            except ValueError as exc:
                self.stdout.write(self.style.WARNING(f"  skipping {filename}: {exc}"))
                continue
            query = np.array(embedding, dtype=np.float32)

            for person in people:
                for face_embedding in person.face_embeddings.all():
                    similarity = float(np.dot(query, np.array(face_embedding.embedding, dtype=np.float32)))
                    (same_person if person.id == owner.id else cross_person).append(similarity)

        return same_person, cross_person

    # ---- reporting -----------------------------------------------------

    def _print_table(self, same_person_similarities, cross_person_similarities):
        table = far_frr_table(same_person_similarities, cross_person_similarities, THRESHOLD_SWEEP)

        self.stdout.write(f"{'threshold':>10} | {'FAR':>8} | {'FRR':>8}")
        self.stdout.write("-" * 34)
        for row in table:
            far = row["far"] if row["far"] is not None else float("nan")
            frr = row["frr"] if row["frr"] is not None else float("nan")
            self.stdout.write(f"{row['threshold']:>10.2f} | {far:>8.2%} | {frr:>8.2%}")

        self.stdout.write(
            "\nFAR = fraction of DIFFERENT people wrongly accepted as a match at that threshold "
            "(lower is safer). FRR = fraction of the SAME person wrongly rejected (lower is more "
            "convenient). Pick the lowest threshold where FAR is acceptable for a security gate - "
            "some FRR is an acceptable cost (the voting/tiebreak logic already covers occasional "
            "single-frame misses)."
        )
