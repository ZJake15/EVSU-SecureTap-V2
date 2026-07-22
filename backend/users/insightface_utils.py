import io
import threading

import cv2
import numpy as np
from django.conf import settings
from django.core.files.base import ContentFile
from insightface.app import FaceAnalysis
from PIL import Image, ImageOps

# Quality-check defaults for enrollment photos specifically - the gate scan
# (compute_face_embeddings_and_boxes) doesn't reject on these outright, since
# a walk-by frame is never going to be studio-quality; it reports blur
# variance instead and lets IdentifyView decide (see GATE_SCAN_MIN_BLUR_VARIANCE).
MIN_BLUR_VARIANCE = 80.0  # Laplacian variance below this reads as visibly blurry
MIN_FACE_AREA_RATIO = 0.02  # face bounding box vs. whole-frame area

_app = None
_app_lock = threading.Lock()
_scan_app = None
_scan_app_lock = threading.Lock()


def _get_app():
    """Lazily loads the full-size InsightFace buffalo_l model once per
    process and reuses it for enrollment, where accuracy on a deliberately
    posed, human-confirmed photo matters more than speed - constructing
    FaceAnalysis is expensive (loads 5 ONNX models), so this must not
    happen per-request."""
    global _app
    if _app is None:
        with _app_lock:
            if _app is None:
                app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
                app.prepare(ctx_id=0, det_size=(640, 640))
                _app = app
    return _app


def _get_scan_app():
    """A second, smaller-input InsightFace instance dedicated to the gate
    scan (compute_face_embeddings_and_boxes). The continuous scan needs to
    keep up with someone walking through at normal pace - detection time is
    the dominant per-poll cost (~0.4-0.9s at the full 640x640 enrollment
    size), so a smaller GATE_SCAN_DET_SIZE trades some far-away-face
    detection range for a meaningfully faster per-frame turnaround, which
    in turn means more real polls land inside the multi-frame voting window
    while someone crosses the gate. Kept as a genuinely separate model
    instance (not just a different det_size on the same one) so enrollment
    quality is never affected by this - costs extra memory (~280MB) for
    the second model, accepted deliberately for this project's scale."""
    global _scan_app
    if _scan_app is None:
        with _scan_app_lock:
            if _scan_app is None:
                app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
                det_size = settings.GATE_SCAN_DET_SIZE
                app.prepare(ctx_id=0, det_size=(det_size, det_size))
                _scan_app = app
    return _scan_app


def load_bgr_array(image_file):
    """Reads image_file (any Pillow-supported format, EXIF-rotation
    corrected) into a BGR numpy array - the format both OpenCV and
    InsightFace expect."""
    image_file.seek(0)
    pil_image = ImageOps.exif_transpose(Image.open(image_file)).convert("RGB")
    rgb = np.array(pil_image)
    return rgb[:, :, ::-1].copy()  # RGB -> BGR


def _blur_variance(bgr_image, box):
    x1, y1, x2, y2 = (int(v) for v in box)
    crop = bgr_image[max(0, y1):y2, max(0, x1):x2]
    if crop.size == 0:
        return 0.0
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def compute_face_embedding(image_file):
    """For enrollment: requires exactly one face, live quality-checked.
    Returns (embedding: list[float] (512-d), detection_score: float).
    Raises ValueError with a human-readable reason on any rejection, so
    callers (DRF serializers, the bulk-import view, the add-photo action)
    can surface it directly to whoever's enrolling."""
    bgr_image = load_bgr_array(image_file)
    faces = _get_app().get(bgr_image)

    if not faces:
        raise ValueError("No face could be detected in the photo. Use a clear, front-facing photo.")
    if len(faces) > 1:
        raise ValueError("Multiple faces were detected in the photo. Use a photo with only one person.")

    face = faces[0]
    frame_area = bgr_image.shape[0] * bgr_image.shape[1]
    x1, y1, x2, y2 = face.bbox
    face_area = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    if frame_area <= 0 or (face_area / frame_area) < MIN_FACE_AREA_RATIO:
        raise ValueError("The face is too small in this photo. Move closer or crop tighter.")

    if _blur_variance(bgr_image, face.bbox) < MIN_BLUR_VARIANCE:
        raise ValueError("This photo looks too blurry. Retake it with steadier hands or better light.")

    return face.normed_embedding.tolist(), float(face.det_score)


def compute_face_embeddings_and_boxes(bgr_image):
    """For the gate scan: every face in the frame, no rejection here - a
    continuous CCTV-style scan has to work with whatever it gets, and
    IdentifyView is the one that decides what's usable (blur, edge-cutoff)
    using settings-configurable thresholds. Returns a list of
    (embedding: list[float], box: (x1, y1, x2, y2) as ints, detection_score:
    float, blur_variance: float) tuples, one per detected face - uses the
    smaller/faster scan-dedicated detector (see _get_scan_app), not the
    full-size enrollment one."""
    faces = _get_scan_app().get(bgr_image)
    results = []
    for face in faces:
        x1, y1, x2, y2 = (int(round(v)) for v in face.bbox)
        box = (x1, y1, x2, y2)
        blur_variance = _blur_variance(bgr_image, box)
        results.append((face.normed_embedding.tolist(), box, float(face.det_score), blur_variance))
    return results


def box_touches_edge(box, image_width, image_height, margin_ratio):
    """True if box is within margin_ratio of any frame edge - a face this
    close to the boundary is likely partially cut off, which produces
    unreliable landmarks/embeddings even when the detector still returns a
    box for it. margin_ratio is relative to the corresponding frame
    dimension (e.g. 0.02 = within 2% of the frame's width/height)."""
    x1, y1, x2, y2 = box
    margin_x = image_width * margin_ratio
    margin_y = image_height * margin_ratio
    return x1 <= margin_x or y1 <= margin_y or x2 >= image_width - margin_x or y2 >= image_height - margin_y


def crop_face(bgr_image, box, padding_ratio=0.4):
    """Crops a face out of a full BGR frame (with a margin so a guard sees
    more than eyes-nose-mouth) and returns it as JPEG bytes, ready to attach
    to an EntryLog's captured_photo."""
    x1, y1, x2, y2 = box
    height, width = bgr_image.shape[:2]
    pad_x = int((x2 - x1) * padding_ratio)
    pad_y = int((y2 - y1) * padding_ratio)
    x1 = max(0, x1 - pad_x)
    y1 = max(0, y1 - pad_y)
    x2 = min(width, x2 + pad_x)
    y2 = min(height, y2 + pad_y)

    rgb_crop = bgr_image[y1:y2, x1:x2, ::-1]
    buffer = io.BytesIO()
    Image.fromarray(rgb_crop).save(buffer, format="JPEG", quality=85)
    return buffer.getvalue()


def normalize_to_jpeg(image_file):
    """Re-encodes image_file as a JPEG (applying EXIF rotation first) for
    storage - engine-agnostic, unchanged from the previous dlib-based
    face_utils.py. Uploads can arrive in formats browsers can't render at
    all (HEIC, most notably); normalizing here means the dashboard's <img>
    thumbnails always work regardless of the original upload format."""
    image_file.seek(0)
    pil_image = ImageOps.exif_transpose(Image.open(image_file)).convert("RGB")
    buffer = io.BytesIO()
    pil_image.save(buffer, format="JPEG", quality=90)
    return ContentFile(buffer.getvalue(), name="reference.jpg")
