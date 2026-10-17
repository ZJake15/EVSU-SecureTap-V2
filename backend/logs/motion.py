"""Following each face from frame to frame at a gate - how fast it moves,
and which earlier face it is - for two parts of the gate scan:

- The fast-movement check (Settings > Photo Quality Checks > "Fast movement
  limit"). A face moving fast is smeared in the photo (motion blur), and a
  smeared face doesn't look enough like its saved photos to match - which
  used to be voted Unknown before the person slowed down. So IdentifyView
  skips such a frame ("Checking..."), the same way it skips a turned or
  blurry one.
- Telling a covered face from an Unknown one: each face's recent frames
  read as covered vs unrecognized (note_covered / covered_share), so the
  Unknown vote holds back for a face that keeps being read as covered.
- Undoing a wrong Unknown. Each face gets a track number that carries over
  while it stays in view; an Unknown recorded on a track that is then
  recognized moments later was that person all along, however smeared the
  first frames were (see IdentifyView._correct_recent_unknowns).

Speed is in face widths per second, so it means the same near and far from
the camera: each face is paired with the nearest unclaimed face in this
gate's previous frame (if that was under MAX_GAP_SECONDS ago and within
MAX_JUMP_WIDTHS), and the distance between their centres is divided by the
face's width and the time between the two frames. Walking toward the camera
barely moves the centre; turning the head fast or moving across the view
does. A face with no recent previous face nearby is new: no speed (None)
and a new track.

Kept in memory per backend process - nothing is saved. The backend runs as
one process (runserver), so every gate's frames pass through here.
"""

import itertools
import math
import threading
import time

# Frames further apart than this aren't compared - the person may have
# left and someone else arrived.
MAX_GAP_SECONDS = 1.0
# The furthest a face can move between two frames and still be "the same
# face" - a fast mover covers ~2.4 widths in 0.3 s at 8 widths/s.
MAX_JUMP_WIDTHS = 3.0
# How long an Unknown is remembered on its track.
UNKNOWN_MEMORY_SECONDS = 30

_lock = threading.Lock()
_last_frame = {}  # gate_location -> (time, [(centre_x, centre_y, width, track_id), ...])
_unknowns = {}  # gate_location -> {track_id: [(time, log_id), ...]}
_covered = {}  # gate_location -> {track_id: [(time, read_as_covered), ...]}
_track_ids = itertools.count(1)


def _shape(box):
    x1, y1, x2, y2 = box
    return (x1 + x2) / 2.0, (y1 + y2) / 2.0, max(1.0, float(x2 - x1))


def track_faces(gate_location, boxes, now=None):
    """One (speed in face widths per second or None, track number) per box,
    in order - and remembers these faces as this gate's latest frame."""
    now = time.monotonic() if now is None else now
    shapes = [_shape(box) for box in boxes]
    with _lock:
        previous = _last_frame.get(gate_location)
        earlier = []
        gap = None
        if previous is not None:
            then, earlier = previous
            gap = now - then
            if gap <= 0 or gap > MAX_GAP_SECONDS:
                earlier = []
        unclaimed = list(earlier)
        tracked = []
        for x, y, width in shapes:
            nearby = [f for f in unclaimed
                      if math.hypot(f[0] - x, f[1] - y) / max(width, f[2]) <= MAX_JUMP_WIDTHS]
            match = min(nearby, key=lambda f: math.hypot(f[0] - x, f[1] - y), default=None)
            if match is None:
                tracked.append((None, next(_track_ids), (x, y, width)))
                continue
            unclaimed.remove(match)
            speed = math.hypot(match[0] - x, match[1] - y) / max(width, match[2]) / gap
            tracked.append((speed, match[3], (x, y, width)))
        _last_frame[gate_location] = (now, [(x, y, w, track_id) for _speed, track_id, (x, y, w) in tracked])
    return [(speed, track_id) for speed, track_id, _shape_ in tracked]


def face_speeds(gate_location, boxes, now=None):
    """Just the speeds of track_faces."""
    return [speed for speed, _track in track_faces(gate_location, boxes, now)]


def remember_unknown(gate_location, track_id, log_id, now=None):
    """An Unknown entry was recorded for the face on this track."""
    now = time.monotonic() if now is None else now
    with _lock:
        tracks = _unknowns.setdefault(gate_location, {})
        for key in [k for k, items in tracks.items() if all(now - t > UNKNOWN_MEMORY_SECONDS for t, _ in items)]:
            del tracks[key]
        tracks.setdefault(track_id, []).append((now, log_id))


def note_covered(gate_location, track_id, covered, now=None):
    """One frame of this face read as covered (True) or as an unrecognized
    face (False) - see covered_share."""
    if track_id is None:
        return
    now = time.monotonic() if now is None else now
    with _lock:
        tracks = _covered.setdefault(gate_location, {})
        for key in [k for k, items in tracks.items() if now - items[-1][0] > UNKNOWN_MEMORY_SECONDS]:
            del tracks[key]
        tracks.setdefault(track_id, []).append((now, covered))


def covered_share(gate_location, track_id, within_seconds, now=None):
    """Of this face's frames in the last within_seconds that were read as
    covered or as unrecognized, the share read as covered - None if none
    were. Lets the Unknown vote ask "is this face being covered?" about the
    face itself, not the whole gate."""
    if track_id is None:
        return None
    now = time.monotonic() if now is None else now
    with _lock:
        items = [covered for t, covered in _covered.get(gate_location, {}).get(track_id, []) if now - t <= within_seconds]
    return sum(items) / len(items) if items else None


def unknowns_on_track(gate_location, track_id, within_seconds, now=None):
    """The Unknown entries recorded on this track in the last
    within_seconds - forgotten once returned."""
    now = time.monotonic() if now is None else now
    with _lock:
        items = _unknowns.get(gate_location, {}).pop(track_id, [])
    return [log_id for t, log_id in items if now - t <= within_seconds]
