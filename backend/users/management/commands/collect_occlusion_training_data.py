import csv
import glob
import os
import shutil
import time
from datetime import datetime

import cv2
import numpy as np
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from PIL import Image, ImageOps

from users.insightface_utils import compute_face_embeddings_and_boxes, crop_face
from users.models import FaceEmbedding

# Matches the actual capture convention already used for guided enrollment
# (GuidedEnrollment.jsx's 5 slots) - tracked here too, purely so a later
# validation pass can ask "does the classifier do worse on slight-smile clean
# photos specifically", the same reasoning DEGRADED_KEYS below already uses
# for its own subtypes.
CLEAN_KEYS = {
    ord("f"): "straight_on",
    ord("l"): "slight_left",
    ord("r"): "slight_right",
    ord("n"): "neutral",
    ord("m"): "slight_smile",
}

# One key per occlusion "flavor" - not because the classifier will ever be
# trained to tell these apart (it only ever sees clean/degraded), but because
# a labeled subtype is worth having later if validation shows one particular
# kind of occlusion is where a trained classifier struggles.
#
# hand_left_eye/hand_right_eye are deliberately still capturable here - see
# EXCLUDED_FROM_TRAINING below for why they're not thrown away, just not fed
# to THIS classifier.
DEGRADED_KEYS = {
    ord("1"): "hand_mouth",
    ord("2"): "hand_nose",
    ord("3"): "hand_left_eye",
    ord("4"): "hand_right_eye",
    ord("5"): "hand_mouth_nose",
}

# The three features this classifier trains on - mouth_visibility_ratio,
# lower_face_texture_ratio, det_score - are ALL specifically about the
# mouth/nose region (see insightface_utils.py). A hand over an eye leaves the
# mouth fully visible, so those two features read just like a clean photo;
# only det_score might dip, and it's the weakest/least specific of the three.
# Labeling an eye-covered photo "degraded" would tell the classifier "here's
# a covered face" while every measurement it actually has says otherwise - a
# contradictory example, not just a hard one, which teaches it a worse
# boundary even for the mouth/nose cases it CAN detect. So these two
# subtypes are still captured and saved (nothing collected today is wasted -
# they're exactly the raw material a genuinely separate future eye-occlusion
# feature would need, built on its own new geometry signal), just excluded
# at training time, not at collection time.
EXCLUDED_FROM_TRAINING = {"hand_left_eye", "hand_right_eye"}

QUIT_KEY = ord("q")
SKIP_KEY = ord("s")  # --label-folder only: "not usable, don't include this one at all"

MANIFEST_COLUMNS = ["filename", "label", "subtype", "det_score", "mouth_ratio", "texture_ratio", "captured_at"]

# Anything PIL can plausibly decode here - HEIC included, since pillow-heif is
# already registered app-wide (see users/apps.py) by the time a management
# command's handle() runs, no different from any other Django view/command.
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".bmp", ".webp"}

# cv2.imshow() sizes its window to the image's exact pixel dimensions, not to
# the screen - a phone's portrait photo (often 3024x4032 or similar) opens a
# preview window far taller than any real monitor, so only the top portion is
# ever visible on-screen (the rest is off-screen, not actually cropped from
# the photo itself). Display-only: this caps just the PREVIEW copy shown in
# the window; detection and the saved crop in _save_sample both still run
# against the original, full-resolution image.
PREVIEW_MAX_DIMENSION = 900


class Command(BaseCommand):
    help = (
        "Interactive webcam tool for collecting labeled clean/degraded (occluded) face images "
        "to train the occlusion-quality classifier - a Random Forest over the same three "
        "geometric/texture signals IdentifyView._occlusion_reason already computes "
        "(mouth_visibility_ratio, lower_face_texture_ratio, det_score), not raw landmark "
        "positions (see insightface_utils.mouth_visibility_ratio's docstring for why that "
        "approach was already tried and specifically found not to work on this detector). "
        "With --harvest-clean, pulls the 'clean' half straight from already-enrolled Person "
        "photos instead of capturing anything new - a real enrollment photo is clean by "
        "construction, since it already passed compute_face_embedding's own single-face/blur/"
        "size checks to get enrolled at all. With --label-folder, reviews a folder of already-"
        "taken photos (not captured through this tool) one at a time in a preview window so you "
        "can label each with a single keypress, instead of manually sorting/renaming files. "
        "Saves a face crop per sample plus its three features into manifest.csv, so training "
        "later needs no re-detection pass. Eye-occlusion subtypes are captured but excluded from "
        "this classifier's training set - see EXCLUDED_FROM_TRAINING."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--output-dir",
            default=os.path.join(settings.BASE_DIR, "occlusion_training_data"),
            help="Where to save labeled images + manifest.csv (default: backend/occlusion_training_data/)",
        )
        parser.add_argument("--camera-index", type=int, default=0)
        parser.add_argument(
            "--harvest-clean",
            action="store_true",
            help="Copy every already-enrolled Person's source photo into clean/ instead of opening the webcam.",
        )
        parser.add_argument(
            "--label-folder",
            help=(
                "Review every photo directly inside this folder one at a time in a preview "
                "window, labeling each with a keypress, instead of opening the webcam. A "
                "labeled photo is moved into <folder>/_labeled/ afterward (so re-running only "
                "ever shows what's left); one with zero or several faces detected, or one you "
                "press 's' on, moves to <folder>/_skipped/ instead - safe to re-run any time, "
                "already-processed photos are never asked about twice."
            ),
        )

    def handle(self, *args, **options):
        if options["harvest_clean"] and options["label_folder"]:
            raise CommandError("Pass only one of --harvest-clean or --label-folder, not both.")

        output_dir = options["output_dir"]
        clean_dir = os.path.join(output_dir, "clean")
        degraded_dir = os.path.join(output_dir, "degraded")
        os.makedirs(clean_dir, exist_ok=True)
        os.makedirs(degraded_dir, exist_ok=True)
        manifest_path = os.path.join(output_dir, "manifest.csv")
        manifest_is_new = not os.path.isfile(manifest_path)

        # Appended to, not overwritten - running this across several short
        # sessions (different days, different people helping capture
        # "degraded" examples) should accumulate into one growing dataset,
        # not reset it each time.
        with open(manifest_path, "a", newline="") as manifest_file:
            writer = csv.DictWriter(manifest_file, fieldnames=MANIFEST_COLUMNS)
            if manifest_is_new:
                writer.writeheader()

            if options["harvest_clean"]:
                self._harvest_clean(clean_dir, writer)
            elif options["label_folder"]:
                self._label_folder(options["label_folder"], clean_dir, degraded_dir, writer)
            else:
                self._capture_live(options["camera_index"], clean_dir, degraded_dir, writer)

    # ---- harvest existing enrollment photos as "clean" ---------------------

    def _harvest_clean(self, clean_dir, writer):
        saved = 0
        skipped = 0
        for face_embedding in FaceEmbedding.objects.select_related("person"):
            if not face_embedding.source_image:
                continue
            try:
                face_embedding.source_image.open("rb")
                image_bytes = face_embedding.source_image.read()
            finally:
                face_embedding.source_image.close()

            bgr_image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
            if bgr_image is None:
                skipped += 1
                continue

            detections = compute_face_embeddings_and_boxes(bgr_image)
            if len(detections) != 1:
                # An enrollment photo is meant to have exactly one face - if
                # this one now finds zero or several, something's off (a
                # corrupted file, a since-changed detector); skip rather than
                # guess which detection is the actual enrolled person.
                skipped += 1
                continue
            _embedding, box, det_score, _blur, _yaw, mouth_ratio, texture_ratio = detections[0]

            filename = f"clean_{face_embedding.person_id}_{face_embedding.id}.jpg"
            with open(os.path.join(clean_dir, filename), "wb") as out:
                out.write(crop_face(bgr_image, box))
            writer.writerow({
                "filename": filename, "label": "clean",
                # Which of the 5 guided-capture poses this was isn't tracked
                # at the FaceEmbedding level - enrollment never recorded it,
                # so this is genuinely unknown, not blank/missing data.
                "subtype": "unknown_pose",
                "det_score": det_score, "mouth_ratio": mouth_ratio, "texture_ratio": texture_ratio,
                "captured_at": datetime.now().isoformat(timespec="seconds"),
            })
            saved += 1

        self.stdout.write(self.style.SUCCESS(f"Harvested {saved} clean sample(s) from existing enrollments."))
        if skipped:
            self.stdout.write(self.style.WARNING(f"Skipped {skipped} embedding(s) - unreadable or not exactly one face."))

    # ---- live webcam capture ------------------------------------------------

    def _capture_live(self, camera_index, clean_dir, degraded_dir, writer):
        cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            raise CommandError(f"Could not open webcam at index {camera_index}.")

        counts = self._empty_counts()
        self.stdout.write(self._instructions())

        try:
            while True:
                success, frame = cap.read()
                if not success:
                    continue

                preview = self._fit_for_preview(frame)
                self._draw_overlay(preview, counts)
                cv2.imshow("Occlusion training data capture - EVSU SecureTap", preview)
                key = cv2.waitKey(1) & 0xFF

                if key == QUIT_KEY:
                    break
                if key in CLEAN_KEYS:
                    self._save_sample(frame, clean_dir, "clean", CLEAN_KEYS[key], writer, counts)
                elif key in DEGRADED_KEYS:
                    self._save_sample(frame, degraded_dir, "degraded", DEGRADED_KEYS[key], writer, counts)
        finally:
            cap.release()
            cv2.destroyAllWindows()

        self.stdout.write(self.style.SUCCESS(f"Done. Counts: {counts}"))

    def _save_sample(self, frame, target_dir, label, subtype, writer, counts):
        # Deliberately the SAME detection call the gate scan itself uses, with
        # no quality gating on top of it here - the whole point of the
        # "degraded" half of this dataset is to capture exactly the low-
        # quality/occluded detections the gate scan has to make a real
        # decision about, not to filter them back out.
        detections = compute_face_embeddings_and_boxes(frame)
        if len(detections) != 1:
            self.stdout.write(self.style.WARNING(
                f"  skipped - {'no' if not detections else 'more than one'} face detected, try again"
            ))
            return
        _embedding, box, det_score, _blur, _yaw, mouth_ratio, texture_ratio = detections[0]

        counts[subtype] = counts.get(subtype, 0) + 1
        filename = f"{label}_{subtype}_{int(time.time() * 1000)}.jpg"
        with open(os.path.join(target_dir, filename), "wb") as out:
            out.write(crop_face(frame, box))
        writer.writerow({
            "filename": filename, "label": label, "subtype": subtype,
            "det_score": det_score, "mouth_ratio": mouth_ratio, "texture_ratio": texture_ratio,
            "captured_at": datetime.now().isoformat(timespec="seconds"),
        })
        self.stdout.write(f"  saved {filename} ({subtype}: {counts[subtype]})")

    @staticmethod
    def _fit_for_preview(image, max_dimension=PREVIEW_MAX_DIMENSION):
        """Downscales a copy of `image` for on-screen display only, if either
        dimension exceeds max_dimension - never upscales a smaller image.
        Called before _draw_overlay so the overlay text's fixed pixel
        coordinates land in sensible spots on the window actually shown,
        rather than being calibrated for one size and drawn on another."""
        height, width = image.shape[:2]
        scale = min(1.0, max_dimension / max(height, width))
        if scale >= 1.0:
            return image.copy()
        new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
        return cv2.resize(image, new_size, interpolation=cv2.INTER_AREA)

    @staticmethod
    def _empty_counts():
        return {
            **{name: 0 for name in CLEAN_KEYS.values()},
            **{name: 0 for name in DEGRADED_KEYS.values()},
            "skipped": 0,
        }

    @staticmethod
    def _draw_overlay(frame, counts):
        lines = [
            "CLEAN:  f=straight-on  l=slight-left  r=slight-right  n=neutral  m=slight-smile",
            "DEGRADED:  1=hand/mouth  2=hand/nose  3=hand/L-eye*  4=hand/R-eye*  5=hand/mouth+nose"
            "   (*collected, not used for training)",
            "s=skip  q=quit",
            "  ".join(f"{name}:{count}" for name, count in counts.items()),
        ]
        for index, line in enumerate(lines):
            cv2.putText(
                frame, line, (10, 22 + index * 22), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1, cv2.LINE_AA
            )

    @staticmethod
    def _instructions():
        return (
            "\nLive capture window opening - keep your face in frame.\n"
            "  CLEAN:     f = straight-on   l = slight left   r = slight right\n"
            "             n = neutral       m = slight smile\n"
            "  DEGRADED:  1 = hand over mouth        2 = hand over nose\n"
            "             3 = hand over left eye     4 = hand over right eye\n"
            "             5 = hand covering mouth and nose\n"
            "  q = quit\n"
            "Note: eye-occlusion photos (3/4) are saved and labeled, but excluded from this "
            "classifier's training set - the mouth/nose features it uses can't discriminate "
            "an eye covering, since the mouth is left fully visible either way. Kept as data for "
            "a possible future, separate eye-occlusion feature.\n"
            "Aim for a mix of head distances/angles/lighting within each category, not all "
            "identical shots - the classifier only generalizes to variation it's actually seen.\n"
        )

    # ---- label a folder of already-taken photos, one at a time -------------

    def _label_folder(self, folder, clean_dir, degraded_dir, writer):
        if not os.path.isdir(folder):
            raise CommandError(f"--label-folder '{folder}' is not a directory.")

        # Labeled/skipped photos are moved out of the source folder as they're
        # handled - so this is naturally resumable (only what's left in
        # `folder` itself gets shown), and re-running never re-asks about a
        # photo already labeled in a previous session.
        processed_dir = os.path.join(folder, "_labeled")
        skipped_dir = os.path.join(folder, "_skipped")
        os.makedirs(processed_dir, exist_ok=True)
        os.makedirs(skipped_dir, exist_ok=True)

        image_paths = sorted(
            path for path in glob.glob(os.path.join(folder, "*"))
            if os.path.isfile(path) and os.path.splitext(path)[1].lower() in IMAGE_EXTENSIONS
        )
        if not image_paths:
            self.stdout.write(self.style.WARNING(
                f"No image files found directly in {folder} "
                "(already-labeled/skipped ones from a previous run don't count - they're in "
                "_labeled/_skipped now)."
            ))
            return

        self.stdout.write(self._label_folder_instructions(len(image_paths)))
        counts = self._empty_counts()

        index = 0
        try:
            while index < len(image_paths):
                path = image_paths[index]
                bgr_image = self._load_as_bgr(path)
                if bgr_image is None:
                    self.stdout.write(self.style.WARNING(
                        f"  [{index + 1}/{len(image_paths)}] unreadable, skipping: {os.path.basename(path)}"
                    ))
                    shutil.move(path, os.path.join(skipped_dir, os.path.basename(path)))
                    counts["skipped"] += 1
                    index += 1
                    continue

                detections = compute_face_embeddings_and_boxes(bgr_image)
                preview = self._fit_for_preview(bgr_image)
                self._draw_overlay(preview, counts)
                cv2.putText(
                    preview, f"[{index + 1}/{len(image_paths)}] {os.path.basename(path)}",
                    (10, preview.shape[0] - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1, cv2.LINE_AA,
                )
                if len(detections) != 1:
                    cv2.putText(
                        preview,
                        f"{'no' if not detections else 'multiple'} face(s) detected - only 's' (skip) works here",
                        (10, 115), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 255), 1, cv2.LINE_AA,
                    )
                cv2.imshow("Label existing photos - EVSU SecureTap", preview)
                key = cv2.waitKey(0) & 0xFF  # waits indefinitely - a static photo, no reason to poll

                if key == QUIT_KEY:
                    self.stdout.write(self.style.WARNING(
                        f"Stopped at photo {index + 1}/{len(image_paths)} - the rest are untouched "
                        "in the source folder, resume anytime with the same command."
                    ))
                    break
                if key == SKIP_KEY:
                    shutil.move(path, os.path.join(skipped_dir, os.path.basename(path)))
                    counts["skipped"] += 1
                    index += 1
                    continue
                if key in CLEAN_KEYS or key in DEGRADED_KEYS:
                    if len(detections) != 1:
                        self.stdout.write(self.style.WARNING(
                            f"  can't label '{os.path.basename(path)}' - "
                            f"{'no' if not detections else 'multiple'} face(s) detected. Skipped instead."
                        ))
                        shutil.move(path, os.path.join(skipped_dir, os.path.basename(path)))
                        counts["skipped"] += 1
                        index += 1
                        continue
                    label = "clean" if key in CLEAN_KEYS else "degraded"
                    subtype = CLEAN_KEYS[key] if key in CLEAN_KEYS else DEGRADED_KEYS[key]
                    target_dir = clean_dir if label == "clean" else degraded_dir
                    self._save_sample(bgr_image, target_dir, label, subtype, writer, counts)
                    shutil.move(path, os.path.join(processed_dir, os.path.basename(path)))
                    index += 1
                    continue
                # Any other key - re-prompt on the SAME photo rather than
                # silently skipping it or guessing what was meant.
                self.stdout.write("  (unrecognized key - press again: f/l/r/n/m/1/2/3/4/5/s/q)")
        finally:
            cv2.destroyAllWindows()

        self.stdout.write(self.style.SUCCESS(f"Done. Counts: {counts}"))

    @staticmethod
    def _load_as_bgr(path):
        """PIL, not cv2.imread - this project's photo pipeline already relies
        on PIL/pillow-heif for HEIC (see insightface_utils.normalize_to_jpeg),
        which cv2.imread can't decode at all. Also applies EXIF rotation, the
        same normalize_to_jpeg already does for every other upload path -
        without it, a phone photo taken in portrait can load sideways."""
        try:
            pil_image = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
        except Exception:
            return None
        rgb_array = np.array(pil_image)
        return np.ascontiguousarray(rgb_array[:, :, ::-1])  # RGB (PIL) -> BGR (OpenCV/insightface)

    @staticmethod
    def _label_folder_instructions(photo_count):
        return (
            f"\nReviewing {photo_count} photo(s) from the source folder, one at a time.\n"
            "  CLEAN:     f=straight-on  l=slight-left  r=slight-right  n=neutral  m=slight-smile\n"
            "  DEGRADED:  1=hand/mouth  2=hand/nose  3=hand/left-eye*  4=hand/right-eye*"
            "  5=hand/mouth+nose\n"
            "  s = skip (not usable)\n"
            "  q = stop here (safe to resume later - already-handled photos won't be asked again)\n"
            "*3/4 (eye occlusion) are saved and labeled but excluded from this classifier's "
            "training set - see EXCLUDED_FROM_TRAINING at the top of this file.\n"
        )
