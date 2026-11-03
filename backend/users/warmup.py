"""Loads the face models as soon as the backend starts.

Each model loads on first use, so the first scans after starting up waited
for them - 1.5-5 s answers, almost all of it on the backend, in the capstone
test's performance log. Started from UsersConfig.ready() under runserver, on
its own thread so the backend starts serving straight away; each model is
also run once, on a blank picture, since a model's first run is slow too.
"""

import logging
import threading
import time

import numpy as np

logger = logging.getLogger(__name__)

_FRAME = (720, 1280, 3)
_BOX = (560, 260, 720, 460)
# Five plausible keypoints inside _BOX (eyes, nose, mouth corners) - the
# fingerprint model aligns the face on them.
_KPS = np.array([[610, 330], [670, 330], [640, 370], [615, 410], [665, 410]], dtype=np.float32)


def start():
    threading.Thread(target=_warm_up, name="face-model-warm-up", daemon=True).start()


def _warm_up():
    from django.db import connection
    from insightface.app.common import Face

    from . import flatness, insightface_utils, liveness_utils

    time.sleep(1.0)  # let runserver finish starting first
    started = time.perf_counter()
    try:
        image = np.zeros(_FRAME, dtype=np.uint8)
        insightface_utils.compute_face_embeddings_and_boxes(image)  # the face finder
        face = Face(bbox=np.array(_BOX, dtype=np.float32), kps=_KPS, det_score=1.0)
        insightface_utils._get_app().models["recognition"].get(image, face)  # the fingerprint model
        liveness_utils.compute_liveness_details(image, _BOX)  # the fake-face models
        flatness.measure("warm-up", 0, image, _BOX)  # the test mode's landmark model, if installed
        flatness._history.pop(("warm-up", 0), None)
        logger.info("Face models loaded in %.1f s.", time.perf_counter() - started)
    except Exception:
        logger.exception("Loading the face models early failed - the first scan will load them instead.")
    finally:
        connection.close()  # this thread's database connection (the fake-face check reads a setting)
