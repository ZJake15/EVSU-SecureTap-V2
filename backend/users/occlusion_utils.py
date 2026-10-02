"""Decides whether a detected face reads as covered (hand, mask, cloth over the
mouth/nose), for IdentifyView._occlusion_reason.

Two interchangeable decision rules over the SAME three measurements, picked by
settings.OCCLUSION_DETECTION_MODE:

- "rules" (default): the original check - flag occlusion if ANY of three fixed
  thresholds trips (mouth width, lower-face texture, detection confidence).
  See IdentifyView._occlusion_reason's docstring and each threshold's comment
  in settings.py for how those were calibrated.
- "classifier": the Random Forest trained by `manage.py
  train_occlusion_classifier` on labeled clean/covered photos. It sees the
  same three numbers, but learns how to weigh them together from examples
  instead of applying three separate hand-set cutoffs.

The classifier is loaded once per process, the same way liveness_utils loads
MiniFASNet. If the file is missing or won't load, that's logged once and
every later check quietly uses "rules" instead - switching the mode on can
never crash or stall the gate scan. A single frame whose mouth/texture
measurement couldn't be taken also falls back to "rules" for that frame only,
since the model was never trained on a missing value and the rule-based check
already knows how to skip a signal it doesn't have.
"""

import logging
import threading

import joblib
import numpy as np
from django.conf import settings
from configuration import store as system_settings

logger = logging.getLogger(__name__)

# The model sees a plain list of numbers, not named columns, so training and
# the live check have to build that list in exactly the same order.
# train_occlusion_classifier imports this constant rather than keeping its own
# copy, so the two can't drift apart.
FEATURE_COLUMNS = ["mouth_ratio", "texture_ratio", "det_score"]

MODE_RULES = "rules"
MODE_CLASSIFIER = "classifier"

# Label the training command uses for "covered" (0 = clean, 1 = degraded).
_DEGRADED_LABEL = 1

_model = None
_model_load_failed = False
_model_lock = threading.Lock()


def _get_model():
    """The trained classifier, loaded on first use and kept for the life of the
    process - or None, permanently, if it couldn't be loaded."""
    global _model, _model_load_failed
    if _model is not None or _model_load_failed:
        return _model
    with _model_lock:
        if _model is None and not _model_load_failed:
            path = settings.OCCLUSION_CLASSIFIER_PATH
            try:
                model = joblib.load(path)
                list(model.classes_).index(_DEGRADED_LABEL)  # a sanity check on what was loaded
                _model = model
            except Exception:
                logger.warning(
                    "Occlusion classifier failed to load from %s - occlusion detection will use the "
                    "rule-based thresholds instead. Run 'manage.py train_occlusion_classifier' to create it.",
                    path, exc_info=True,
                )
                _model_load_failed = True
    return _model


def rule_based_occluded(mouth_ratio, texture_ratio, det_score):
    """The original check: covered if ANY of the three signals trips. A
    measurement that couldn't be taken (None) is simply left out, never
    counted as a trip."""
    mouth_flagged = mouth_ratio is not None and mouth_ratio < system_settings.get("covered_rule_mouth")
    texture_flagged = texture_ratio is not None and texture_ratio < system_settings.get("covered_rule_texture")
    det_score_flagged = det_score < system_settings.get("covered_rule_det_score")
    return mouth_flagged or texture_flagged or det_score_flagged


def classifier_occlusion_probability(mouth_ratio, texture_ratio, det_score):
    """The classifier's 0-1 estimate that this face is covered, or None when it
    can't give one for this frame (model unavailable, or a measurement missing)."""
    if mouth_ratio is None or texture_ratio is None:
        return None
    model = _get_model()
    if model is None:
        return None
    values = {"mouth_ratio": mouth_ratio, "texture_ratio": texture_ratio, "det_score": det_score}
    features = np.array([[float(values[column]) for column in FEATURE_COLUMNS]], dtype=np.float64)
    degraded_column = list(model.classes_).index(_DEGRADED_LABEL)
    return float(model.predict_proba(features)[0][degraded_column])


def is_occluded(mouth_ratio, texture_ratio, det_score):
    """The single yes/no IdentifyView acts on, using whichever rule
    OCCLUSION_DETECTION_MODE selects (with the fallbacks described above)."""
    if system_settings.get("covered_face_mode") == MODE_CLASSIFIER:
        probability = classifier_occlusion_probability(mouth_ratio, texture_ratio, det_score)
        if probability is not None:
            return probability >= system_settings.get("covered_face_cutoff")
    return rule_based_occluded(mouth_ratio, texture_ratio, det_score)


def active_mode_description():
    """What's actually making the decision right now, in plain words - shown on
    the dashboard's Settings page so a silent fallback is visible."""
    configured = system_settings.get("covered_face_mode")
    if configured == MODE_CLASSIFIER:
        if _get_model() is not None:
            return "In use now: Trained model."
        return "In use now: Simple rules - the trained model's file is missing or unreadable."
    return "In use now: Simple rules."
