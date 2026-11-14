"""A guard signing in at the gate monitor by face (Settings -> "Sign in with
face scan"), from the sign-in window's view of the gate's entrance camera.

Which faces count: the guard accounts linked to a staff record (Accounts
page) whose face photos are on file, and who may sign in at this gate
(gate_shifts.refusal - a Security Officer only at their assigned gate). With
"Card, then face", only the account whose staff card was just tapped here.

The largest face in the picture is the one checked; it must pass the same
fake-face check as everyone at the gate, match at the usual Match strictness,
and do so on FACE_FRAMES_NEEDED frames in a row - one good frame is never
enough, the same idea as an entry.
"""

import threading
import time

import numpy as np

from accounts.models import AdminProfile
from configuration import store as system_settings
from users import insightface_utils, liveness_utils

from . import gate_shifts
from .models import GateShift

FACE_FRAMES_NEEDED = 2
FRAME_GAP_SECONDS = 4.0  # frames further apart than this don't count as "in a row"
_streaks = {}  # gate -> (user_id, frames, monotonic time)
_lock = threading.Lock()


def _name(user):
    return user.get_full_name() or user.username


def _refused(reason, code, **extra):
    return {"success": False, "reason": reason, "reason_code": code, **extra}


def _reset(gate_location):
    with _lock:
        _streaks.pop(gate_location, None)


def _candidates(gate_location, card_profile):
    """[(profile, embeddings matrix)] of the guards who may sign in here by face."""
    if card_profile is not None:
        profiles = [card_profile]
    else:
        profiles = (AdminProfile.objects.select_related("user", "staff_person")
                    .filter(staff_person__isnull=False, user__is_active=True))
    found = []
    for profile in profiles:
        person = profile.staff_person
        if person is None or not person.is_active or gate_shifts.refusal(profile.user, gate_location):
            continue
        rows = [face.embedding for face in person.face_embeddings.all()]
        if rows:
            found.append((profile, np.array(rows, dtype=np.float32)))
    return found


def sign_in(gate_location, bgr_image, token=None):
    """One frame from the sign-in window. Returns {"success": True, "name",
    "gate_sign_in"} once the guard is signed in, else {"success": False,
    "reason", "reason_code"} - shown under the camera view while it keeps trying."""
    ways = gate_shifts.methods()
    if not gate_shifts.enabled() or not ways["face"]:
        return _refused("Signing in by face is switched off on the Settings page.", "face_off")
    card_profile = None
    if ways["card_then_face"]:
        card_profile = gate_shifts.card_step_user(gate_location, token)
        if card_profile is None:
            return _refused("Tap your staff ID card first.", "card_first")

    detections = insightface_utils.compute_face_embeddings_and_boxes(bgr_image) if bgr_image is not None else []
    if not detections:
        _reset(gate_location)
        return _refused("Look at the camera.", "no_face")
    embedding, box = max(detections, key=lambda d: (d[1][2] - d[1][0]) * (d[1][3] - d[1][1]))[:2]

    if system_settings.get("spoof_check_enabled"):
        score = liveness_utils.compute_liveness_details(bgr_image, box)["score"]
        if score < system_settings.get("spoof_strictness"):
            _reset(gate_location)
            return _refused("That doesn't look like a live face - look at the camera yourself.", "spoof")

    candidates = _candidates(gate_location, card_profile)
    if not candidates:
        _reset(gate_location)
        if card_profile is not None:
            return _refused(f"{_name(card_profile.user)}'s account isn't linked to a staff record with face photos "
                            "(Accounts page) - sign in another way.", "no_faces")
        return _refused("No guard who may sign in here has face photos on file (Accounts page: Staff record).",
                        "no_faces")
    query = np.array(embedding, dtype=np.float32)
    best_profile, best = max(((profile, float((rows @ query).max())) for profile, rows in candidates),
                             key=lambda pair: pair[1])
    if best < system_settings.get("match_strictness"):
        _reset(gate_location)
        if card_profile is not None:
            return _refused(f"That face isn't {_name(card_profile.user)}'s.", "face_mismatch")
        return _refused("Not recognized as a guard who can sign in here.", "not_recognized")

    now = time.monotonic()
    with _lock:
        user_id, frames, at = _streaks.get(gate_location, (None, 0, 0.0))
        frames = frames + 1 if user_id == best_profile.user_id and now - at <= FRAME_GAP_SECONDS else 1
        _streaks[gate_location] = (best_profile.user_id, frames, now)
    if frames < FACE_FRAMES_NEEDED:
        return _refused("Hold still…", "checking", name=_name(best_profile.user))

    _reset(gate_location)
    gate_shifts.end_card_step(gate_location)
    method = GateShift.Method.CARD_FACE if card_profile is not None else GateShift.Method.FACE
    gate_shifts.start_shift(best_profile.user, gate_location, method)
    return {"success": True, "name": _name(best_profile.user), "gate_sign_in": gate_shifts.status(gate_location)}
