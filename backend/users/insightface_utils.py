import io
import os
import threading

import cv2
import numpy as np
import onnxruntime as ort
from django.conf import settings
from django.core.files.base import ContentFile
from insightface.app import FaceAnalysis
from insightface.app.common import Face
from PIL import Image, ImageOps

# Quality-check defaults for enrollment photos specifically - the gate scan
# (compute_face_embeddings_and_boxes) doesn't reject on these outright, since
# a walk-by frame is never going to be studio-quality; it reports blur
# variance instead and lets IdentifyView decide (see GATE_SCAN_MIN_BLUR_VARIANCE).
MIN_BLUR_VARIANCE = 80.0  # Laplacian variance below this reads as visibly blurry
MIN_FACE_AREA_RATIO = 0.02  # face bounding box vs. whole-frame area

_app = None
_app_lock = threading.Lock()

# Enrollment looks for faces at this size; the gate scan at GATE_SCAN_DET_SIZE.
ENROLLMENT_DET_SIZE = 640


# buffalo_s (not buffalo_l): same 512-d ArcFace embedding space either way,
# but buffalo_l's recognition model (w600k_r50, a ResNet-50) benchmarked at
# ~900ms/face on this project's target hardware (a fanless low-power laptop
# CPU, no GPU) - buffalo_s's w600k_mbf (MobileFaceNet) is the same job on a
# much cheaper backbone. Enrollment and the gate scan MUST use the same pack:
# they compare embeddings from each other via cosine similarity, which is
# only meaningful within one model's embedding space. If this ever changes
# again, every stored FaceEmbedding needs regenerating from its source_image
# (see the recompute_embeddings management command) before scanning will
# match anything again.
_MODEL_PACK = "buffalo_s"

# This pack bundles 5 ONNX models (detection, recognition, genderage,
# landmark_2d_106, landmark_3d_68), but FaceAnalysis.get() runs every loaded
# model on every detected face regardless of whether anything downstream
# reads its output. Neither compute_face_embedding nor
# compute_face_embeddings_and_boxes ever touch face.gender/age/landmark_* -
# only bbox, det_score and normed_embedding - so loading/running the other 3
# is pure wasted CPU time on every enrollment and every ~0.2s gate scan.
_REQUIRED_MODULES = ["detection", "recognition"]


def ai_threads():
    """How many CPU cores each AI model may use per run (settings.AI_THREADS,
    or automatic: half the processors, at most 4)."""
    configured = getattr(settings, "AI_THREADS", 0)
    if configured and configured > 0:
        return configured
    return max(1, min(4, (os.cpu_count() or 2) // 2))


def ai_session_options():
    """ONNX Runtime options shared by every model here: capped to ai_threads()
    and run one step at a time, so a frame never spawns more workers than
    the laptop has cores to spare."""
    options = ort.SessionOptions()
    options.intra_op_num_threads = ai_threads()
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    return options


def _get_app():
    """The InsightFace models, loaded once per process and shared by both
    enrollment and the gate scan - constructing FaceAnalysis is expensive
    (it loads the ONNX models), so this must not happen per request.

    One copy serves both because the face finder can be asked to search at
    a different size on every call (see _find_faces): enrollment searches a
    posed photo at ENROLLMENT_DET_SIZE, the gate scan at the smaller, faster
    GATE_SCAN_DET_SIZE. That used to be two separate copies of the models.

    InsightFace builds its model sessions without any way to pass options,
    so each one is rebuilt here from the same model file with the shared
    thread cap (ai_session_options) - same model, same results, just not
    allowed to take every core."""
    global _app
    if _app is None:
        with _app_lock:
            if _app is None:
                app = FaceAnalysis(
                    name=_MODEL_PACK, providers=["CPUExecutionProvider"], allowed_modules=_REQUIRED_MODULES
                )
                for model in app.models.values():
                    model.session = ort.InferenceSession(
                        model.model_file, sess_options=ai_session_options(), providers=["CPUExecutionProvider"]
                    )
                app.prepare(ctx_id=0, det_size=(ENROLLMENT_DET_SIZE, ENROLLMENT_DET_SIZE))
                _app = app
    return _app


def _find_faces(bgr_image, det_size):
    """FaceAnalysis.get(), but searching at `det_size` for this one call -
    every detected face gets its bounding box, keypoints, detection score
    and normalized embedding, exactly as FaceAnalysis.get() would give."""
    app = _get_app()
    bboxes, kpss = app.det_model.detect(bgr_image, input_size=(det_size, det_size), max_num=0, metric="default")
    faces = []
    for i in range(bboxes.shape[0]):
        face = Face(bbox=bboxes[i, 0:4], kps=kpss[i] if kpss is not None else None, det_score=bboxes[i, 4])
        for taskname, model in app.models.items():
            if taskname != "detection":
                model.get(bgr_image, face)
        faces.append(face)
    return faces


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
    faces = _find_faces(bgr_image, ENROLLMENT_DET_SIZE)

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


def head_yaw_ratio(kps):
    """How far a face is turned away from the camera, derived from the
    detector's own 5 keypoints (both eyes, nose tip, both mouth corners) - so
    it costs nothing beyond the detection that already ran, and needs no
    extra ONNX model loaded (this pack deliberately loads only detection and
    recognition - see _REQUIRED_MODULES).

    Measures how far the nose tip sits from the midpoint between the eyes,
    along the eye-to-eye axis, as a fraction of the inter-eye distance. A
    face looking straight at the camera puts its nose tip almost exactly
    between the eyes (~0.0); turning the head slides the nose toward the
    trailing eye, and by roughly 30 degrees the ratio clears 0.35. Measuring
    along the eye axis rather than the image's x-axis makes this independent
    of head tilt - a head cocked sideways but still facing the camera scores
    ~0.0, as it should.

    Pitch (looking up or down) is deliberately NOT measured here. With only 5
    keypoints it can't be separated from the camera's mounting height: a
    gate camera above head height makes every single person read as
    "looking down", so a fixed pitch threshold would reject everyone at one
    gate and nobody at another. Yaw has no such bias.

    Returns None when the keypoints are missing or degenerate (both eyes
    detected at the same spot), so callers can fall through to their existing
    behaviour rather than reject a face on a measurement that never happened.
    """
    if kps is None or len(kps) < 3:
        return None
    points = np.asarray(kps, dtype=np.float64)
    eye_left, eye_right, nose = points[0], points[1], points[2]
    eye_vector = eye_right - eye_left
    eye_distance = float(np.linalg.norm(eye_vector))
    if eye_distance < 1e-6:
        return None
    eye_axis = eye_vector / eye_distance
    nose_offset = nose - (eye_left + eye_right) / 2.0
    return abs(float(np.dot(nose_offset, eye_axis))) / eye_distance


def mouth_visibility_ratio(kps):
    """A proxy for whether the mouth/nose are occluded (a hand, mask, or high
    collar), derived from the same 5 keypoints head_yaw_ratio uses - so, like
    that function, it costs nothing beyond the detection that already ran.

    IMPORTANT - this is NOT a per-landmark confidence score, and InsightFace
    exposes no such thing anywhere in this installation - checked directly in
    the installed package source, not assumed. This pack's detector
    (SCRFD/det_500m, the buffalo_s detection model) outputs exactly one
    confidence value per face (det_score, the box-level detection confidence
    - see IdentifyView._occlusion_reason for how that's used as a separate
    signal) plus 5 keypoint *coordinates* with no per-point uncertainty
    attached. The richer landmark model this pack also bundles but doesn't
    load (landmark_2d_106/landmark_3d_68 - see _REQUIRED_MODULES) is the same
    story: pure coordinate regression (optionally a 3-axis pose estimate),
    still no visibility/confidence output anywhere.

    An earlier version of this check assumed a covered feature would produce
    an *implausible* landmark arrangement (eyes not level, nose not between/
    below them, mouth corners not symmetric below the nose) and planned to
    check that directly. Verified empirically instead of assumed, and it
    does NOT hold: painting a skin-toned patch over the mouth/nose of real
    enrolled photos and re-running this exact detector, every plausibility
    measure (eye level, eye-distance-to-box-size, nose offset from the eyes,
    mouth position/symmetry relative to the nose) came back statistically
    indistinguishable between clean and occluded faces. SCRFD's regression
    head doesn't produce a distorted-looking guess for a covered feature -
    it produces a *plausible-looking but wrong* one, geometrically
    self-consistent with the rest of the face. Plausibility alone doesn't
    detect that.

    What DOES move, on the same test: mouth WIDTH specifically (measured
    below) and how texture-rich the lower face looks (see
    lower_face_texture_ratio) - because a hand or mask genuinely narrows
    the space between the mouth corners and flattens the region's texture,
    even while its shape stays "plausible". The gap between "clearly
    visible" and "clearly covered" on mouth width is still much thinner than
    FACE_MAX_YAW_RATIO's (that one had a >10x separation on the same kind of
    test; this one does not) - so treat this as a rough proxy, not a
    validated occlusion classifier, and keep FACE_MIN_MOUTH_VISIBILITY_
    RATIO's default conservative (biased toward under-detecting rather than
    misreading a visible face as occluded - see that setting's own comment).

    On its own this also badly under-detects partial coverage - real-world
    testing found a hand covering just the mouth/chin (not reaching the
    nose) very often doesn't shrink this ratio enough to trip a threshold
    that can't be loosened further without misreading visible faces as
    covered. That's why IdentifyView combines THREE independent signals -
    this ratio, lower_face_texture_ratio, and det_score - and flags
    occlusion if *any one* fires, since each misses different real cases.

    Measures mouth width (the two mouth-corner keypoints) as a fraction of
    the inter-eye distance - a real, unobstructed mouth sits in a fairly
    consistent proportion to eye spacing; a hand or mask over it collapses
    that width toward the point where the coordinates end up nearly
    coincident. Only meaningful on a face that's already roughly frontal -
    yaw foreshortens mouth width on its own (for the same reason
    head_yaw_ratio exists), which is why IdentifyView checks yaw before this.

    Returns None when keypoints are missing/degenerate, mirroring
    head_yaw_ratio's contract, so callers fall through rather than flag
    occlusion on a measurement that never happened.
    """
    if kps is None or len(kps) < 5:
        return None
    points = np.asarray(kps, dtype=np.float64)
    eye_left, eye_right, mouth_left, mouth_right = points[0], points[1], points[3], points[4]
    eye_distance = float(np.linalg.norm(eye_right - eye_left))
    if eye_distance < 1e-6:
        return None
    mouth_width = float(np.linalg.norm(mouth_right - mouth_left))
    return mouth_width / eye_distance


def lower_face_texture_ratio(bgr_image, box):
    """A second, independent occlusion signal - added after real-world
    testing showed mouth_visibility_ratio alone misses a lot of genuine
    partial occlusion (a hand covering just the mouth/chin, not reaching the
    nose). That check trusts SCRFD's regressed mouth-corner *positions*
    under occlusion; this one doesn't trust the keypoints at all, only the
    face BOUNDING BOX (which degrades far less under occlusion than fine
    landmarks) - so it catches real cases the geometry check misses, because
    the two fail in different ways.

    Reuses the exact Laplacian-variance technique _blur_variance already
    uses, but compares two regions of the SAME face instead of the whole
    frame against a fixed threshold: the lower third (mouth/chin) against the
    upper-middle (forehead/eyes, always visible). A real, unobstructed mouth
    is texture-rich (lips, teeth edges, the shadow under the nose) relative
    to the rest of the face; a hand or flat cloth over it is comparatively
    smooth, so the ratio drops. Normalizing against the same face's own upper
    region (rather than a fixed absolute variance) means per-photo
    lighting/sharpness differences mostly wash out.

    Verified empirically alongside mouth_visibility_ratio (see that
    function's docstring for the test setup): on the same 10 photos, clean
    ran 0.463-1.103; occlusion patterns that mouth_visibility_ratio's
    threshold missed entirely - a hand covering only the mouth/chin, not the
    nose - showed 0.000-0.328 here, well below the clean floor. Not a
    guarantee either (a beard, heavy shadow, or a very smooth-skinned mouth
    could plausibly read low without anything covering it) - see
    FACE_MAX_MOUTH_TEXTURE_RATIO's own comment.

    A third, independent signal (det_score, the detector's own per-face
    confidence) is checked alongside this and mouth_visibility_ratio in
    IdentifyView._occlusion_reason - see that method for why, and why a
    fourth idea (landmark-arrangement "plausibility") was tested and
    dropped.

    Returns None if the box is too small/degenerate to sample meaningfully,
    same fall-through contract as the other two ratio functions.
    """
    x1, y1, x2, y2 = box
    height = y2 - y1
    if height <= 0:
        return None
    upper = bgr_image[y1 + int(height * 0.10):y1 + int(height * 0.45), x1:x2]
    lower = bgr_image[y1 + int(height * 0.60):y2, x1:x2]
    if upper.size == 0 or lower.size == 0:
        return None
    upper_variance = cv2.Laplacian(cv2.cvtColor(upper, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()
    lower_variance = cv2.Laplacian(cv2.cvtColor(lower, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()
    if upper_variance < 1e-6:
        return None
    return float(lower_variance / upper_variance)


def compute_face_embeddings_and_boxes(bgr_image):
    """For the gate scan: every face in the frame, no rejection here - a
    continuous CCTV-style scan has to work with whatever it gets, and
    IdentifyView is the one that decides what's usable (blur, edge-cutoff,
    turned away, occluded) using settings-configurable thresholds. Returns a
    list of (embedding: list[float], box: (x1, y1, x2, y2) as ints,
    detection_score: float, blur_variance: float, yaw_ratio: float | None,
    mouth_ratio: float | None, texture_ratio: float | None) tuples, one per
    detected face - uses the smaller/faster scan-dedicated detector (see
    _get_scan_app), not the full-size enrollment one."""
    faces = _find_faces(bgr_image, settings.GATE_SCAN_DET_SIZE)
    results = []
    for face in faces:
        x1, y1, x2, y2 = (int(round(v)) for v in face.bbox)
        box = (x1, y1, x2, y2)
        blur_variance = _blur_variance(bgr_image, box)
        kps = getattr(face, "kps", None)
        yaw_ratio = head_yaw_ratio(kps)
        mouth_ratio = mouth_visibility_ratio(kps)
        texture_ratio = lower_face_texture_ratio(bgr_image, box)
        results.append((
            face.normed_embedding.tolist(), box, float(face.det_score), blur_variance,
            yaw_ratio, mouth_ratio, texture_ratio,
        ))
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


# Photo uploads are restricted to these formats. Pillow can decode plenty more
# (WEBP, BMP, TIFF, GIF, and AVIF via the same plugin that provides HEIF), but
# "whatever Pillow happens to open" isn't a contract anyone can rely on, and
# these cover every phone camera, screenshot and scanner a registrar will
# actually hand us.
#
# "HEIF" is the format name Pillow reports for a .heic file - HEIC is a
# specific packaging of HEIF, and pillow-heif maps .heic/.heif/.hif all onto
# the one format string (see users/apps.py, which registers the opener).
# .avif reports as "AVIF" and is deliberately NOT on this list.
ALLOWED_UPLOAD_FORMATS = ("JPEG", "PNG", "HEIF")

# What to call them in user-facing errors: nobody with an iPhone calls their
# photos HEIF. Derived from one place so the message can't drift from the list.
ALLOWED_UPLOAD_LABEL = "JPEG, PNG and HEIC"


def ensure_supported_image_format(image_file):
    """Raises ValueError unless image_file really is a JPEG, PNG or HEIC.

    Decides on the file's actual decoded format, not its filename extension
    or the browser-supplied Content-Type - both are trivially wrong (a
    renamed .webp, a phone that mislabels HEIC) and neither is something a
    server should trust for a policy decision. Pillow only reads the header
    to answer this, so it costs nothing next to the face detection that
    follows. Leaves the file rewound either way, since every caller goes on
    to read it.

    Returns the detected format name, so callers can log or echo it.
    """
    image_file.seek(0)
    try:
        image_format = Image.open(image_file).format
    except Exception:
        image_format = None
    finally:
        image_file.seek(0)

    if image_format is None:
        raise ValueError(
            f"That file isn't a readable image. Only {ALLOWED_UPLOAD_LABEL} photos are accepted."
        )
    if image_format not in ALLOWED_UPLOAD_FORMATS:
        raise ValueError(
            f"Only {ALLOWED_UPLOAD_LABEL} photos are accepted - this file is {image_format}."
        )
    return image_format


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
