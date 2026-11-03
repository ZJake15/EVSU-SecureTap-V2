"""Watch-only "flat face" measure for the capstone test mode - it's
recorded, never used to decide anything.

A printed photo is flat: between two frames its landmark points move the way
one flat sheet can (a homography). A real head is 3D: when it turns even a
little, its nose and eyes shift against each other in a way no flat sheet's
movement explains. On recordings of one person (record_face_motion), when
the face had turned enough to judge, a held photo's movement fit a flat
sheet much better than the real head's did at 2-3 m; a photo held steady
rarely turns enough to judge at all.

For each face, followed from frame to frame by its track number
(logs/motion.py), the 106 landmark points of every checked frame are kept
for WINDOW_SECONDS, in memory only. Each new frame is compared with the
earlier frame (at least MIN_GAP_SECONDS back) whose shape differs most:
shape change = what's left after the best shift, in-picture turn and size
change, as a fraction of the face width. Under MIN_SHAPE_CHANGE the face
hasn't turned enough to tell - no score. Otherwise flat score = what's left
after the best flat-sheet fit / the shape change: low means it moved like a
flat sheet; under FLAT_CUT it's reported as flat. FLAT_CUT is a first guess
from those recordings - the test mode records the score itself too.
"""

import threading
import time
from collections import deque
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort
from django.conf import settings
from insightface.app.common import Face

from . import insightface_utils

LANDMARK_MODEL = "2d106det.onnx"  # in the same model pack (buffalo_s) as the gate's two
INNER = slice(33, 106)  # eyebrows, eyes, nose, mouth - not the face outline
WINDOW_SECONDS = 1.0
MIN_GAP_SECONDS = 0.3
MIN_SHAPE_CHANGE = 0.01
FLAT_CUT = 0.40
FORGET_SECONDS = 5.0  # a track not seen for this long is dropped

_lock = threading.Lock()
_model = None
_model_missing = False
_history = {}  # (gate_location, track_id) -> deque of (time, points, face width)


def _get_model():
    """The 106-point landmark model, loaded once - None if it isn't installed."""
    global _model, _model_missing
    if _model is None and not _model_missing:
        with _lock:
            if _model is None and not _model_missing:
                path = Path(settings.INSIGHTFACE_ROOT) / "models" / insightface_utils._MODEL_PACK / LANDMARK_MODEL
                if not path.is_file():
                    _model_missing = True
                    return None
                from insightface.model_zoo import get_model

                model = get_model(str(path), providers=["CPUExecutionProvider"])
                # The same thread cap as the gate's other models (insightface_utils._get_app).
                model.session = ort.InferenceSession(
                    model.model_file, sess_options=insightface_utils.ai_session_options(),
                    providers=["CPUExecutionProvider"],
                )
                model.prepare(ctx_id=-1)
                _model = model
    return _model


def _rms(a, b):
    return float(np.sqrt(((a - b) ** 2).sum(axis=1).mean()))


def _shape_change(a, b):
    """What's left between two sets of points after the best shift, in-picture turn and size change."""
    matrix, _ = cv2.estimateAffinePartial2D(a, b, method=cv2.LMEDS)
    return None if matrix is None else _rms(a @ matrix[:, :2].T + matrix[:, 2], b)


def _plane_residual(a, b):
    """What's left after the best flat-sheet (homography) movement maps a onto b."""
    homography, _ = cv2.findHomography(a, b, 0)
    if homography is None:
        return None
    return _rms(cv2.perspectiveTransform(a.reshape(-1, 1, 2), homography).reshape(-1, 2), b)


def measure(gate_location, track_id, bgr_image, box, now=None):
    """This frame of this face: {"change": shape change or None, "score": flat
    score or None, "flat": bool}, or None if the 106-point model isn't
    installed or the face has no track."""
    model = _get_model()
    if model is None or track_id is None:
        return None
    face = Face(bbox=np.array(box, dtype=np.float32), kps=None, det_score=1.0)
    model.get(bgr_image, face)
    points = np.asarray(face.landmark_2d_106, dtype=np.float32)[INNER]
    width = max(1.0, float(box[2] - box[0]))
    now = time.monotonic() if now is None else now
    with _lock:
        for key in [key for key, kept in _history.items() if not kept or now - kept[-1][0] > FORGET_SECONDS]:
            del _history[key]
        kept = _history.setdefault((gate_location, track_id), deque())
        while kept and now - kept[0][0] > WINDOW_SECONDS:
            kept.popleft()
        earlier = [(old, old_width) for t, old, old_width in kept if now - t >= MIN_GAP_SECONDS]
        kept.append((now, points, width))

    best = None
    for old, old_width in earlier:
        change = _shape_change(old, points)
        if change is not None and (best is None or change / old_width > best[0]):
            best = (change / old_width, old, old_width)
    if best is None:
        return {"change": None, "score": None, "flat": False}
    change, old, old_width = best
    if change < MIN_SHAPE_CHANGE:
        return {"change": round(change, 4), "score": None, "flat": False}
    residual = _plane_residual(old, points)
    if residual is None:
        return {"change": round(change, 4), "score": None, "flat": False}
    score = residual / old_width / change
    return {"change": round(change, 4), "score": round(score, 3), "flat": score < FLAT_CUT}
