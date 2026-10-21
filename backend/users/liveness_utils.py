"""Passive liveness (anti-spoofing) scoring for a single 2D webcam frame.

Hardware constraint: the gate camera is a standard 2D webcam - no depth or
infrared sensor is available, so a depth-map liveness check (the most robust
approach against photo/screen attacks) isn't possible on this hardware.
Everything here is "silent"/passive - a verdict from one frame, no challenge-
response (blink/turn-your-head) prompt to the person at the gate.

Two complementary signals are combined into one score:

1. MiniFASNet (from the Silent-Face-Anti-Spoofing project by Minivision
   AI, Apache-2.0) - two small trained CNNs (MiniFASNetV2 on a 2.7x crop,
   MiniFASNetV1SE on a 4.0x crop, averaged as the project combines them)
   that classify an 80x80 face crop as live / print-attack / replay-attack. This is the primary signal: it's
   specifically trained on print/screen spoof data, which is exactly the
   attack this gate is most exposed to. Run here as a from-source ONNX
   export of the official PyTorch weights (see liveness_models/NOTICE.md for
   exact provenance, hashes, and the verification performed before this was
   trusted) via onnxruntime, which was already a project dependency (pulled
   in transitively by insightface) - no new package to install.
2. The three classical texture/frequency/reflectance cues this module had
   before MiniFASNet was added (still documented in detail below) - kept as
   a secondary, always-available signal. They matter for two reasons: they
   still contribute a fraction of the final score even when MiniFASNet is
   confident, and if the ONNX model ever fails to load (missing/corrupt
   file), compute_liveness_score() transparently falls back to these alone
   rather than breaking the gate scan.

Why not rely on MiniFASNet alone: it's explicitly a *single-frame* method,
trained primarily against photo/screen attacks - it isn't reliable against a
3D/silicone mask, and can struggle against a very steady, high-quality video
replay held right up to the camera. Combining it with independent per-frame
texture/frequency/color cues (different failure modes than a CNN trained on
a specific attack dataset) is one more layer, not redundancy. Note this
project's existing multi-frame majority-vote confirmation (see
logs.views.IdentifyView) is a separate, general-purpose noise-reduction
mechanism applied to every kind of gate-scan result (matches, non-matches,
*and* this liveness score) - it is not itself a liveness signal (no blink/
motion-consistency check is implemented), so don't describe it as one.

Classical cues (secondary signal, sub-scores of CLASSICAL_WEIGHT below):

1. Micro-texture analysis (Local Binary Patterns). Real skin has fine-grained
   texture variance (pores, subsurface light scattering) that a printed photo
   or a screen's surface doesn't reproduce - an unnaturally *uniform* LBP
   texture reads as more likely spoofed.
   Reference: Maatta, Hadid & Pietikainen, "Face spoofing detection from
   single images using micro-texture analysis," IJCB 2011.

2. Frequency-domain analysis (2D FFT high-frequency energy ratio). Printed
   photos (halftone dot patterns) and screen recaptures (pixel-grid moire)
   both introduce periodic high-frequency artifacts a real face under normal
   camera conditions doesn't have.
   Reference: Li, Wang, Cui et al., "Live face detection based on the
   analysis of Fourier spectra," Proc. SPIE 5404, 2004.

3. Color/reflectance analysis (HSV saturation + specular-highlight ratio).
   Printed paper and LCD/phone screens reflect light differently than skin,
   producing distinctive saturation and highlight distributions.
   Reference: Boulkenafet, Komulainen & Hadid, "Face anti-spoofing based on
   color texture analysis," ICIP 2015.
"""

import logging
import os
import threading

import cv2
import numpy as np
import onnxruntime as ort

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MiniFASNet (primary signal) - see liveness_models/NOTICE.md for
# provenance/verification.
# ---------------------------------------------------------------------------

_MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "liveness_models")
_MINIFASNET_CROP_SIZE = 80
# The two official models, as the Silent-Face-Anti-Spoofing project runs
# them together (its test.py adds both models' softmax outputs - so the
# combined P(live) is their average). Each was trained on its own wider,
# re-centered context crop around the detected face - the scale encoded in
# its filename ("2.7_80x80", "4_0_0_80x80"; their src/utility.py
# parse_model_name). A tight face-only crop meaningfully hurts accuracy
# (verified when the 2.7 model was integrated). The 4.0 model sees more of
# the surroundings - the edge of a printed sheet or a phone, the hand
# holding it - which is what a close, sharp print gives away.
MINIFASNET_MODELS = (
    ("2.7", os.path.join(_MODEL_DIR, "2.7_80x80_MiniFASNetV2.onnx"), 2.7),
    ("4.0", os.path.join(_MODEL_DIR, "4.0_80x80_MiniFASNetV1SE.onnx"), 4.0),
)

_sessions = {}
_failed_models = set()
_session_lock = threading.Lock()


def _get_session(name, path):
    """Lazily loads one model's ONNX session once per process, the same
    singleton pattern insightface_utils uses for its FaceAnalysis instances.
    Returns None (permanently, for the life of this process) if the model
    file is missing or fails to load - logged once, not on every gate-scan
    frame - so the score falls back to the other model, or to the classical
    cues alone, instead of crashing the scan loop."""
    if name in _sessions or name in _failed_models:
        return _sessions.get(name)
    with _session_lock:
        if name not in _sessions and name not in _failed_models:
            try:
                # Same thread cap as the face models - see
                # insightface_utils.ai_session_options.
                from users.insightface_utils import ai_session_options

                _sessions[name] = ort.InferenceSession(
                    path, sess_options=ai_session_options(), providers=["CPUExecutionProvider"]
                )
            except Exception:
                logger.warning(
                    "MiniFASNet %s ONNX model failed to load from %s - passive liveness will use the other "
                    "model and/or the classical texture/frequency/reflectance scoring only.",
                    name, path, exc_info=True,
                )
                _failed_models.add(name)
    return _sessions.get(name)


def _enabled_models():
    """Settings > Anti-Spoofing: "Fake-face check models" - both (default)
    or only the 2.7 one, as before the 4.0 model was added."""
    try:
        from configuration import store

        if store.get("spoof_models") == "one":
            return MINIFASNET_MODELS[:1]
    except Exception:
        pass
    return MINIFASNET_MODELS


def _minifasnet_crop(bgr_image, box, crop_scale=2.7):
    """Expands the detected face box by crop_scale around its center
    (clamped to the frame), then resizes to 80x80 - replicates the official
    repo's CropImage._get_new_box/crop exactly. Returns None for a degenerate
    box rather than raising."""
    x1, y1, x2, y2 = box
    box_w, box_h = x2 - x1, y2 - y1
    if box_w <= 0 or box_h <= 0:
        return None
    src_h, src_w = bgr_image.shape[:2]

    scale = min((src_h - 1) / box_h, min((src_w - 1) / box_w, crop_scale))
    new_w, new_h = box_w * scale, box_h * scale
    center_x, center_y = x1 + box_w / 2, y1 + box_h / 2
    left, top = center_x - new_w / 2, center_y - new_h / 2
    right, bottom = center_x + new_w / 2, center_y + new_h / 2

    if left < 0:
        right -= left
        left = 0
    if top < 0:
        bottom -= top
        top = 0
    if right > src_w - 1:
        left -= right - (src_w - 1)
        right = src_w - 1
    if bottom > src_h - 1:
        top -= bottom - (src_h - 1)
        bottom = src_h - 1

    left, top, right, bottom = int(left), int(top), int(right), int(bottom)
    if right <= left or bottom <= top:
        return None
    crop = bgr_image[top : bottom + 1, left : right + 1]
    if crop.size == 0:
        return None
    return cv2.resize(crop, (_MINIFASNET_CROP_SIZE, _MINIFASNET_CROP_SIZE))


def _minifasnet_scores(bgr_image, box):
    """P(live) in [0, 1] from each enabled MiniFASNet model that's available
    ({"2.7": ..., "4.0": ...}); a model that's missing, or a degenerate crop,
    is simply left out."""
    scores = {}
    for name, path, crop_scale in _enabled_models():
        session = _get_session(name, path)
        if session is None:
            continue
        crop = _minifasnet_crop(bgr_image, box, crop_scale)
        if crop is None:
            continue
        # Deliberately NOT divided by 255 - the official repo's custom
        # to_tensor() has that normalization commented out in its source;
        # the models were trained on raw [0,255] BGR values. Dividing by 255
        # here silently breaks them (verified during integration - see
        # liveness_models/NOTICE.md). BGR is correct too: cv2 crops are
        # already BGR and the original pipeline never converts to RGB.
        tensor = crop.astype(np.float32).transpose(2, 0, 1)[None, ...]
        logits = session.run(None, {"input": tensor})[0][0]
        probabilities = np.exp(logits - logits.max())
        probabilities /= probabilities.sum()
        scores[name] = float(probabilities[1])  # index 1 = "real" class (see official repo's test.py)
    return scores


def _minifasnet_score(bgr_image, box):
    """The models' combined P(live) - their average, as the official method
    combines them - or None if no model is available."""
    scores = _minifasnet_scores(bgr_image, box)
    return sum(scores.values()) / len(scores) if scores else None


# ---------------------------------------------------------------------------
# Classical texture/frequency/reflectance cues (secondary signal + fallback)
# ---------------------------------------------------------------------------

# Relative contribution of each classical cue to the classical sub-score.
# Kept as named weights (not baked into the math) so they're easy to re-tune
# independently once real spoof-attempt data exists - same "starting point,
# not validated" status as FACE_MATCH_SIMILARITY_THRESHOLD elsewhere in this
# project.
TEXTURE_WEIGHT = 0.40
FREQUENCY_WEIGHT = 0.35
REFLECTANCE_WEIGHT = 0.25

# How the classical sub-score and MiniFASNet are combined into the final
# score. MiniFASNet gets the larger share since it's a trained classifier
# aimed directly at this project's most likely attack (print/screen), while
# the classical cues stay meaningful as an always-on secondary signal and as
# the fallback if the model can't be loaded at all.
MINIFASNET_WEIGHT = 0.65
CLASSICAL_WEIGHT = 0.35

# LBP texture entropy (in bits, max 8 for a 256-bin histogram) below this is
# treated as "suspiciously flat" - a starting point pending real calibration.
_TEXTURE_ENTROPY_MIN = 3.5
_TEXTURE_ENTROPY_MAX = 6.5

# Ratio of FFT energy sitting in the high-frequency annulus. Real faces
# should sit low; a value here or above reads as fully "not live" for the
# frequency cue specifically.
_FREQUENCY_RATIO_MIN = 0.015
_FREQUENCY_RATIO_MAX = 0.09

# Fraction of the face crop that looks like a specular highlight (very
# bright, low-saturation pixels) - screens and glossy prints tend to produce
# more of this than skin does under normal, diffuse gate lighting.
_SPECULAR_RATIO_MIN = 0.01
_SPECULAR_RATIO_MAX = 0.12


def compute_liveness_score(bgr_image, box):
    """Returns a float in [0, 1] - higher means more likely a real, live
    face. Combines MiniFASNet (primary) with the classical texture/
    frequency/reflectance cues (secondary), falling back to the classical
    score alone if MiniFASNet is unavailable. Compared against
    settings.LIVENESS_SCORE_THRESHOLD the same way
    FACE_MATCH_SIMILARITY_THRESHOLD is used for identity matching (higher =
    better in both cases). Never raises - a crop too small/degenerate to
    analyze scores 0.0 (treated as not live) rather than crashing the scan
    loop over one bad frame."""
    return compute_liveness_details(bgr_image, box)["score"]


def compute_liveness_details(bgr_image, box):
    """compute_liveness_score's parts: {"score": the final score, "classical":
    the texture/frequency/reflectance sub-score, "2.7"/"4.0": each MiniFASNet
    model's P(live)} - the capstone test mode records them per trial, so the
    two models can be compared and the threshold tuned on real data."""
    crop = _face_crop(bgr_image, box)
    if crop is None or crop.size == 0 or min(crop.shape[:2]) < 16:
        return {"score": 0.0}

    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    classical_score = float(
        TEXTURE_WEIGHT * _texture_score(gray)
        + FREQUENCY_WEIGHT * _frequency_score(gray)
        + REFLECTANCE_WEIGHT * _reflectance_score(crop)
    )

    model_scores = _minifasnet_scores(bgr_image, box)
    if not model_scores:
        return {"score": classical_score, "classical": classical_score}
    minifasnet_score = sum(model_scores.values()) / len(model_scores)
    return {
        "score": float(MINIFASNET_WEIGHT * minifasnet_score + CLASSICAL_WEIGHT * classical_score),
        "classical": classical_score,
        **model_scores,
    }


def _face_crop(bgr_image, box):
    x1, y1, x2, y2 = (int(round(v)) for v in box)
    height, width = bgr_image.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(width, x2), min(height, y2)
    if x2 <= x1 or y2 <= y1:
        return None
    return bgr_image[y1:y2, x1:x2]


def _normalize(value, low, high):
    """Linearly maps value from [low, high] to [0, 1], clamped at both ends."""
    if high <= low:
        return 0.0
    return float(np.clip((value - low) / (high - low), 0.0, 1.0))


def _texture_score(gray):
    """Local Binary Pattern micro-texture entropy. A basic 8-neighbor LBP
    (radius 1) computed with vectorized array shifts - no scikit-image
    dependency needed for this simple, fixed-radius case. Real skin's fine
    texture produces a spread-out LBP histogram (higher entropy); a flat
    printout or a screen's surface tends to produce a narrower, more
    repetitive one (lower entropy)."""
    # Downscale for speed and to smooth out sensor noise that would
    # otherwise inflate texture entropy on *every* frame, real or not.
    small = cv2.resize(gray, (96, 96), interpolation=cv2.INTER_AREA)
    center = small[1:-1, 1:-1].astype(np.int16)
    offsets = [
        (-1, -1), (-1, 0), (-1, 1),
        (0, 1), (1, 1), (1, 0), (1, -1), (0, -1),
    ]
    lbp = np.zeros_like(center, dtype=np.uint8)
    for bit, (dy, dx) in enumerate(offsets):
        neighbor = small[1 + dy : small.shape[0] - 1 + dy, 1 + dx : small.shape[1] - 1 + dx].astype(np.int16)
        lbp |= ((neighbor >= center).astype(np.uint8)) << bit

    histogram, _ = np.histogram(lbp, bins=256, range=(0, 256))
    probabilities = histogram.astype(np.float64) / max(1, histogram.sum())
    probabilities = probabilities[probabilities > 0]
    entropy = float(-np.sum(probabilities * np.log2(probabilities)))

    return _normalize(entropy, _TEXTURE_ENTROPY_MIN, _TEXTURE_ENTROPY_MAX)


def _frequency_score(gray):
    """2D FFT high-frequency energy ratio. Printed halftone dot patterns and
    screen pixel-grid moire both concentrate extra energy in a mid/high
    spatial-frequency annulus that a real face, photographed directly by a
    camera under normal conditions, doesn't produce as strongly. A HIGH
    ratio means MORE suspicious artifacts, so the raw ratio is inverted
    before returning (this function follows the same "higher = more live"
    convention as the other two)."""
    small = cv2.resize(gray, (128, 128), interpolation=cv2.INTER_AREA).astype(np.float32)
    spectrum = np.fft.fftshift(np.fft.fft2(small))
    magnitude = np.abs(spectrum)

    center_y, center_x = 64, 64
    y_grid, x_grid = np.ogrid[:128, :128]
    radius = np.sqrt((y_grid - center_y) ** 2 + (x_grid - center_x) ** 2)

    # Excludes the very center (overall brightness/DC component, irrelevant
    # here) and the extreme corners (near-Nyquist sensor noise present in
    # every frame regardless of spoofing) - the band in between is where
    # halftone/moire artifacts actually show up.
    band = (radius >= 12) & (radius <= 50)
    high_frequency_energy = float(magnitude[band].sum())
    total_energy = float(magnitude.sum()) or 1.0
    ratio = high_frequency_energy / total_energy

    return 1.0 - _normalize(ratio, _FREQUENCY_RATIO_MIN, _FREQUENCY_RATIO_MAX)


def _reflectance_score(bgr_crop):
    """HSV saturation + specular-highlight-pixel ratio. Screens and glossy
    prints tend to produce more near-white, low-saturation "hot spot" pixels
    under typical indoor/gate lighting than real skin does. A HIGH specular
    ratio means MORE suspicious highlighting, so this is inverted the same
    way _frequency_score is."""
    hsv = cv2.cvtColor(bgr_crop, cv2.COLOR_BGR2HSV)
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]

    specular_mask = (value > 235) & (saturation < 40)
    specular_ratio = float(np.count_nonzero(specular_mask)) / specular_mask.size

    return 1.0 - _normalize(specular_ratio, _SPECULAR_RATIO_MIN, _SPECULAR_RATIO_MAX)
