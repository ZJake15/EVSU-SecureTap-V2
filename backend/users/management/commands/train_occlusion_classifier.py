import csv
import os

import joblib
import numpy as np
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import train_test_split

from users.management.commands.collect_occlusion_training_data import EXCLUDED_FROM_TRAINING

# The exact three inputs the classifier sees - same three signals
# IdentifyView._occlusion_reason already computes per frame (see
# insightface_utils.py), in a fixed order so a saved model and a fresh
# feature vector always line up the same way.
FEATURE_COLUMNS = ["mouth_ratio", "texture_ratio", "det_score"]

# Below this many usable rows (after filtering), a train/test split is too
# small to mean anything - not a hard ML law, just a sanity floor so a
# 6-photo test run doesn't produce a "100% accurate" result that's really
# just 1-2 held-out examples getting lucky.
MIN_TOTAL_SAMPLES = 20
MIN_PER_CLASS = 8

DEFAULT_MANIFEST = os.path.join(settings.BASE_DIR, "occlusion_training_data", "manifest.csv")
DEFAULT_MODEL_OUTPUT = os.path.join(settings.BASE_DIR, "users", "occlusion_classifier.joblib")


class Command(BaseCommand):
    help = (
        "Trains a small Random Forest to classify a face as clean/degraded from the same "
        "three geometric/texture features IdentifyView._occlusion_reason already computes "
        "(mouth_visibility_ratio, lower_face_texture_ratio, det_score) - see "
        "collect_occlusion_training_data for how manifest.csv is built. Prints a held-out "
        "evaluation (precision/recall/confusion matrix) for the trained classifier AND for "
        "the current rule-based OR-of-three-thresholds logic on the exact same test rows, so "
        "the two can be honestly compared before anything changes in the live pipeline. Saves "
        "the trained model to disk either way - saving is NOT the same as using it live; "
        "wiring a saved model into IdentifyView is a deliberate, separate step, not automatic."
    )

    def add_arguments(self, parser):
        parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
        parser.add_argument("--output", default=DEFAULT_MODEL_OUTPUT)
        parser.add_argument("--test-size", type=float, default=0.2)
        parser.add_argument("--random-state", type=int, default=42)
        parser.add_argument(
            "--exclude-subtype", action="append", default=[], dest="exclude_subtypes", metavar="SUBTYPE",
            help=(
                "Drop every manifest row of this clean/degraded subtype before training (e.g. "
                "--exclude-subtype neutral). Repeatable - pass it more than once to drop several "
                "at once. This is separate from EXCLUDED_FROM_TRAINING (hand-over-eye rows, always "
                "dropped - see collect_occlusion_training_data.py): that set is permanent, because "
                "those features genuinely can't detect eye coverage at all. This flag is for "
                "trying a run without a specific subtype and comparing the result - nothing about "
                "the manifest or the collected photos is touched, so leaving it off next time "
                "brings that subtype straight back."
            ),
        )

    def handle(self, *args, **options):
        rows = self._load_manifest(options["manifest"])
        exclude_subtypes = set(options["exclude_subtypes"])
        X, y, excluded_eye, excluded_manual, incomplete = self._build_dataset(rows, exclude_subtypes)

        self._report_dataset_summary(rows, X, y, excluded_eye, excluded_manual, incomplete, exclude_subtypes)
        self._check_enough_data(y)

        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=options["test_size"], random_state=options["random_state"], stratify=y,
        )

        classifier = RandomForestClassifier(
            n_estimators=200, max_depth=6, class_weight="balanced", random_state=options["random_state"],
        )
        classifier.fit(X_train, y_train)

        self.stdout.write(self.style.SUCCESS(
            f"\nTrained on {len(X_train)} examples, evaluating on {len(X_test)} held-out examples "
            "the model never saw during training.\n"
        ))

        self._print_comparison(classifier, X_test, y_test)
        self._print_feature_importances(classifier)

        joblib.dump(classifier, options["output"])
        self.stdout.write(self.style.SUCCESS(f"\nModel saved to {options['output']}"))
        self.stdout.write(
            "This is a saved file only - nothing in the live gate scan uses it yet. Wiring it "
            "into IdentifyView (behind a setting, compared against the rule-based logic on real "
            "production data) is a separate step, done deliberately, not automatically by this "
            "command."
        )

    # ---- loading + building the training set --------------------------------

    def _load_manifest(self, path):
        if not os.path.isfile(path):
            raise CommandError(
                f"No manifest found at {path} - run 'manage.py collect_occlusion_training_data' "
                "first to build one."
            )
        with open(path, newline="") as manifest_file:
            return list(csv.DictReader(manifest_file))

    def _build_dataset(self, rows, exclude_subtypes=frozenset()):
        """Turns manifest.csv rows into (X, y) arrays ready for sklearn.
        y: 0 = clean, 1 = degraded. Three things get a row excluded rather
        than used: an eye-occlusion subtype (see EXCLUDED_FROM_TRAINING - the
        mouth/nose features here can't discriminate that case, and including
        it would teach the classifier a contradictory example, not a hard
        one), a subtype named in --exclude-subtype for this one run (an ad
        hoc, user-chosen exclusion - e.g. dropping "neutral" clean photos to
        see whether they were diluting that class), or a row missing one of
        the three features (mouth_ratio/texture_ratio can be blank when the
        detector's keypoints were missing/degenerate for that capture - see
        head_yaw_ratio's contract in insightface_utils.py)."""
        X, y = [], []
        excluded_eye = 0
        excluded_manual = 0
        incomplete = 0
        for row in rows:
            if row["label"] == "degraded" and row["subtype"] in EXCLUDED_FROM_TRAINING:
                excluded_eye += 1
                continue
            if row["subtype"] in exclude_subtypes:
                excluded_manual += 1
                continue
            try:
                features = [float(row[column]) for column in FEATURE_COLUMNS]
            except (KeyError, ValueError, TypeError):
                incomplete += 1
                continue
            X.append(features)
            y.append(1 if row["label"] == "degraded" else 0)
        return (
            np.array(X, dtype=np.float64), np.array(y, dtype=np.int64),
            excluded_eye, excluded_manual, incomplete,
        )

    def _report_dataset_summary(self, rows, X, y, excluded_eye, excluded_manual, incomplete, exclude_subtypes):
        clean_count = int(np.sum(y == 0)) if len(y) else 0
        degraded_count = int(np.sum(y == 1)) if len(y) else 0
        lines = [
            f"manifest.csv: {len(rows)} total row(s)",
            f"  usable for training: {len(X)}  (clean: {clean_count}, degraded: {degraded_count})",
            f"  excluded (eye occlusion, see EXCLUDED_FROM_TRAINING): {excluded_eye}",
        ]
        if exclude_subtypes:
            subtype_list = ", ".join(sorted(exclude_subtypes))
            lines.append(f"  excluded (--exclude-subtype {subtype_list}): {excluded_manual}")
        lines.append(f"  excluded (missing a feature value): {incomplete}")
        self.stdout.write("\n".join(lines) + "\n")

    def _check_enough_data(self, y):
        if len(y) < MIN_TOTAL_SAMPLES:
            raise CommandError(
                f"Only {len(y)} usable row(s) - need at least {MIN_TOTAL_SAMPLES} to get a "
                "train/test split that means anything. Collect more with "
                "collect_occlusion_training_data and try again."
            )
        for class_name, class_value in (("clean", 0), ("degraded", 1)):
            count = int(np.sum(y == class_value))
            if count < MIN_PER_CLASS:
                raise CommandError(
                    f"Only {count} usable '{class_name}' example(s) - need at least "
                    f"{MIN_PER_CLASS} of each class. Collect more of that specific kind."
                )

    # ---- evaluation: trained classifier vs. today's rule-based logic -------

    def _print_comparison(self, classifier, X_test, y_test):
        y_pred_model = classifier.predict(X_test)
        y_pred_rules = self._rule_based_predict(X_test)

        self.stdout.write(self.style.SUCCESS("=== Trained classifier (Random Forest) ==="))
        self.stdout.write(classification_report(y_test, y_pred_model, target_names=["clean", "degraded"]))
        self.stdout.write(f"Confusion matrix [[TN, FP], [FN, TP]] (positive = degraded):\n{confusion_matrix(y_test, y_pred_model)}\n")

        self.stdout.write(self.style.SUCCESS("=== Current rule-based logic (same test rows) ==="))
        self.stdout.write(classification_report(y_test, y_pred_rules, target_names=["clean", "degraded"]))
        self.stdout.write(f"Confusion matrix [[TN, FP], [FN, TP]] (positive = degraded):\n{confusion_matrix(y_test, y_pred_rules)}\n")

        self.stdout.write(
            "Read this as: does the trained classifier's 'degraded' recall/precision beat the "
            "rule-based row on the SAME held-out photos? If not, the extra complexity of a "
            "trained model isn't earning its keep yet - collecting more data (especially more "
            "variety within each degraded subtype) is the usual next step, not changing the "
            "model itself.\n"
        )

    @staticmethod
    def _rule_based_predict(X_test):
        """Reimplements IdentifyView._occlusion_reason's exact OR-of-three-
        thresholds logic against the SAME feature columns/order, so the
        comparison above is genuinely apples-to-apples - the same rows, the
        same three numbers, just two different decision rules applied to
        them."""
        mouth_ratio, texture_ratio, det_score = X_test[:, 0], X_test[:, 1], X_test[:, 2]
        mouth_flagged = mouth_ratio < settings.FACE_MIN_MOUTH_VISIBILITY_RATIO
        texture_flagged = texture_ratio < settings.FACE_MAX_MOUTH_TEXTURE_RATIO
        det_score_flagged = det_score < settings.FACE_MIN_DET_SCORE_UNOCCLUDED
        return (mouth_flagged | texture_flagged | det_score_flagged).astype(np.int64)

    def _print_feature_importances(self, classifier):
        self.stdout.write(self.style.SUCCESS("Feature importances (higher = more influence on the classifier's decision):"))
        pairs = sorted(zip(FEATURE_COLUMNS, classifier.feature_importances_), key=lambda pair: -pair[1])
        for name, importance in pairs:
            self.stdout.write(f"  {name}: {importance:.3f}")
