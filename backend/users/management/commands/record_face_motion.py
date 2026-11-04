"""Records how a face's landmarks move - for building a "flat face" check.

    python manage.py record_face_motion --label real --distance 2
    python manage.py record_face_motion --label photo --distance 3 --seconds 30

A printed photo is flat: when it moves, all its points move together, the
way one sheet does. A real head is 3D: its nose shifts against its cheeks
and ears as it turns even a little. To tell the two apart, this records the
face's 106 landmark points (the contour, eyes, nose, mouth) on every frame,
found exactly as the gate finds faces: the gate's camera code and zoom, the
picture shrunk to the size sent for face checks, the same face finder. The
106-point model comes in the same model pack (buffalo_s) as the two the gate
uses.

Made to be run alone: a countdown with beeps gives time to walk to the
mark, then it records (one long beep at the start and two at the end). Only
numbers are saved, never a picture: one CSV per run in the test results
folder (face-motion/), named after the label and distance. The gate monitor
has to be closed first - one program at a time can use the camera.

    python manage.py record_face_motion --label photo --distance 2 --save-frames

--save-frames also keeps the pictures themselves, for comparing fake-face
models offline: about every SAVE_EVERY_SECONDS, exactly the JPEG the gate
would send to the backend (zoomed, shrunk, same quality), in a folder named
like the CSV. These are pictures of whoever stands there - delete the folder
once the comparison is done.
"""

import csv
import sys
import time
import winsound
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from insightface.app.common import Face
from insightface.model_zoo import get_model

from users import insightface_utils

LANDMARK_MODEL = "2d106det.onnx"
SAVE_EVERY_SECONDS = 0.15  # --save-frames: about the gate's own pace of face checks
HEADER = (["t_s", "frame_w", "frame_h", "faces", "x1", "y1", "x2", "y2", "det_score"]
          + [f"k{i}{axis}" for i in range(5) for axis in "xy"]
          + [f"l{i}{axis}" for i in range(106) for axis in "xy"])


def _output_folder():
    base = Path(settings.DATA_DIR) if settings.DATA_DIR else settings.BASE_DIR.parent / "entry-agent"
    return base / "test-results" / "face-motion"


def _beep(times=1, length_ms=150):
    for _ in range(times):
        winsound.Beep(1200, length_ms)
        time.sleep(0.1)


class Command(BaseCommand):
    help = ("Records a face's 106 landmark points over time (real face or held photo) - numbers only, unless "
            "--save-frames also keeps the pictures.")

    def add_arguments(self, parser):
        parser.add_argument("--label", required=True, help='What is in front of the camera, e.g. "real" or "photo".')
        parser.add_argument("--distance", type=float, required=True, help="Metres from the camera (the floor mark).")
        parser.add_argument("--seconds", type=float, default=20.0, help="How long to record (default 20).")
        parser.add_argument("--countdown", type=float, default=12.0, help="Seconds to walk to the mark (default 12).")
        parser.add_argument("--camera", type=int, default=0, help="Camera number, as in the gate monitor's list.")
        parser.add_argument("--zoom", type=float, default=2.0, help="Camera zoom, as the gate uses it (default 2).")
        parser.add_argument("--resolution", default="2560x1440", help="Camera resolution (default 2560x1440).")
        parser.add_argument("--upload", type=int, default=1280, help="Detail sent for face checks, px (default 1280).")
        parser.add_argument("--save-frames", action="store_true",
                            help="Also save the pictures the gate would send (JPEG), beside the CSV - for comparing "
                                 "fake-face models offline. Delete them afterwards.")

    def handle(self, *args, **options):
        sys.path.insert(0, str(settings.BASE_DIR.parent / "entry-agent"))
        import camera as gate_camera

        landmark_path = Path(settings.INSIGHTFACE_ROOT) / "models" / insightface_utils._MODEL_PACK / LANDMARK_MODEL
        if not landmark_path.is_file():
            raise CommandError(f"The 106-point model isn't here: {landmark_path}")
        landmarks = get_model(str(landmark_path), providers=["CPUExecutionProvider"])
        landmarks.prepare(ctx_id=-1)
        detector = insightface_utils._get_app().det_model

        folder = _output_folder()
        stem = f"{options['label']}_{options['distance']:g}m_{datetime.now():%Y%m%d-%H%M%S}"
        frames_dir = folder / stem if options["save_frames"] else None
        if frames_dir:
            frames_dir.mkdir(parents=True)

        cam = gate_camera.Camera(index=options["camera"], max_upload_dimension=options["upload"], zoom=options["zoom"])
        cam.configure({"camera_resolution": options["resolution"], "camera_fps": "30", "camera_exposure": "auto",
                       "backlight_compensation": "auto", "camera_focus": "auto"})
        cam.start()
        try:
            started = time.monotonic()
            while cam.capture_size() is None:
                if time.monotonic() - started > 30:
                    raise CommandError("No picture from the camera in 30 s - is the gate monitor still open?")
                time.sleep(0.1)
            report = cam.report()
            self.stdout.write(f"Camera {report.get('resolution')} via {report.get('driver')}, zoom {options['zoom']:g}x. "
                              f"Walk to the {options['distance']:g} m mark - recording starts in "
                              f"{options['countdown']:.0f} s.")
            end_countdown = time.monotonic() + options["countdown"]
            while time.monotonic() < end_countdown:
                left = end_countdown - time.monotonic()
                if left <= 3.2:
                    _beep()
                    time.sleep(max(0.0, min(1.0, left) - 0.25))
                else:
                    time.sleep(0.5)
            _beep(length_ms=600)
            rows = self._record(cam, gate_camera._cap_dimension, detector, landmarks, options, frames_dir)
            _beep(times=2)
        finally:
            cam.stop()

        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{stem}.csv"
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(HEADER)
            writer.writerows(rows)
        with_face = [row for row in rows if row[3]]
        widths = sorted(row[6] - row[4] for row in with_face)
        self.stdout.write(self.style.SUCCESS(
            f"Saved {path}\n{len(rows)} frames ({len(rows) / options['seconds']:.1f} a second), a face in "
            f"{len(with_face)}" + (f", face about {widths[len(widths) // 2]:.0f} px wide." if widths
                                   else " - NO FACE FOUND, check the mark.")
            + (f"\n{len(list(frames_dir.glob('*.jpg')))} pictures in {frames_dir}" if frames_dir else "")
        ))

    @staticmethod
    def _record(cam, shrink, detector, landmarks, options, frames_dir=None):
        """One row per frame: the biggest face's box, 5 points and 106 points.
        With frames_dir, also the gate's JPEG about every SAVE_EVERY_SECONDS."""
        rows = []
        last_frame_number = None
        last_saved = -SAVE_EVERY_SECONDS
        started = time.monotonic()
        while time.monotonic() - started < options["seconds"]:
            # Each camera frame once: the latest frame stays the same until the
            # camera delivers the next (Camera counts them as it reads).
            frame_number = cam._frames_since_open
            frame = cam.get_preview_frame() if frame_number != last_frame_number else None
            if frame is None:
                time.sleep(0.005)
                continue
            last_frame_number = frame_number
            t = round(time.monotonic() - started, 3)
            if frames_dir and t - last_saved >= SAVE_EVERY_SECONDS:
                jpeg, _size = cam.capture_jpeg()  # exactly what the gate sends the backend
                (frames_dir / f"{t:08.3f}.jpg").write_bytes(jpeg)
                last_saved = t
            frame = shrink(frame, options["upload"])  # as sent for face checks
            height, width = frame.shape[:2]
            boxes, points = detector.detect(frame, input_size=(settings.GATE_SCAN_DET_SIZE,) * 2, max_num=0,
                                            metric="default")
            if boxes.shape[0] == 0:
                rows.append([t, width, height, 0] + [""] * (len(HEADER) - 4))
                continue
            i = max(range(boxes.shape[0]), key=lambda n: (boxes[n, 2] - boxes[n, 0]) * (boxes[n, 3] - boxes[n, 1]))
            face = Face(bbox=boxes[i, 0:4], kps=points[i] if points is not None else None, det_score=boxes[i, 4])
            landmarks.get(frame, face)
            kps = [round(float(v), 2) for v in face.kps.reshape(-1)] if face.kps is not None else [""] * 10
            rows.append([t, width, height, int(boxes.shape[0])]
                        + [round(float(v), 1) for v in boxes[i, 0:4]] + [round(float(boxes[i, 4]), 3)]
                        + kps + [round(float(v), 2) for v in face.landmark_2d_106.reshape(-1)])
        return rows
