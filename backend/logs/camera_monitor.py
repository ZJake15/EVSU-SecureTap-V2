"""Live gate cameras for the dashboard's Cameras page (Admin only).

A gate monitor sends its camera picture here only while an Admin has the
Cameras page open (entry-agent/monitor_stream.py asks once a second whether
anyone is watching), and the page gets every gate it shows over one long
response (GateCameraStreamView) - one connection for all the gates, so a
browser's limit of ~6 connections per address is never reached.

Kept in memory only: the newest picture of each gate, replaced by the next
one. Nothing is written to disk or the database - the page is live viewing,
not recording (the entry logs already keep a photo of every entry, Unknown
and Spoof). The backend runs as one process (manage.py runserver), so a
module-level store is shared by every request.

The face boxes come from the gate's own face checks (/api/identify - see
note_results), not from a second analysis: the same faces, labels and
colours as the guard's gate monitor, about a check behind the picture.

The stream's format (the dashboard's GateCameras.jsx reads it): a series of
messages, each a 4-byte big-endian length, that many bytes of JSON, then
`size` bytes of JPEG when the JSON says so:
    {"type": "frame", "gate": ..., "size": n, "faces": [...], "faces_age": s}
    {"type": "status", "gates": {gate: {"live": bool, "age": s, "fps": n}}}
"""

import json
import struct
import threading
import time
import uuid
from collections import deque

# A gate keeps sending this long after a page stops asking - and a page whose
# connection died without notice stops counting as a viewer after this long.
WATCH_GRACE_SECONDS = 5.0
# Boxes older than this aren't drawn - the face check is behind or stopped.
FACES_FRESH_SECONDS = 1.0
# No picture for this long: the page shows "No signal".
LIVE_FOR_SECONDS = 3.0
# A status message at least this often, so the page knows the connection is
# alive and a dead connection is noticed.
STATUS_EVERY_SECONDS = 1.0
# One stream lasts at most this long; the page then reconnects by itself, so
# the sign-in is checked again (a deactivated Admin stops seeing the gates).
STREAM_SECONDS = 300
MAX_FRAME_BYTES = 2_000_000
MAX_GATES_PER_STREAM = 16

_lock = threading.Condition()
_frames = {}   # gate -> {"jpeg": bytes, "seq": int, "at": monotonic, "arrivals": deque}
_faces = {}    # gate -> {"at": monotonic, "faces": [...]}
_viewers = {}  # viewer id -> {"gates": set, "seen": monotonic}
_seq = 0


def watched(gate):
    """Whether any open Cameras page shows this gate right now."""
    now = time.monotonic()
    with _lock:
        return any(gate in viewer["gates"] and now - viewer["seen"] < WATCH_GRACE_SECONDS
                   for viewer in _viewers.values())


def put_frame(gate, jpeg):
    """A gate's newest picture. Kept only while someone is watching."""
    global _seq
    if not jpeg or len(jpeg) > MAX_FRAME_BYTES or not jpeg.startswith(b"\xff\xd8"):
        return False
    if not watched(gate):
        return False
    with _lock:
        _seq += 1
        entry = _frames.setdefault(gate, {"arrivals": deque(maxlen=30)})
        entry.update(jpeg=jpeg, seq=_seq, at=time.monotonic())
        entry["arrivals"].append(entry["at"])
        _lock.notify_all()
    return True


def _face(item, width, height):
    """One /api/identify result -> a box (0-1 of the picture), a kind and a
    label - the same words and colours as the gate monitor's boxes
    (entry-agent/ui.py _box_style)."""
    box = item.get("box")
    if not box or not width or not height:
        return None
    if item.get("spoof_suspected"):
        kind, label = "spoof", "SPOOF" if not item.get("retry") else "CHECKING · POSSIBLE SPOOF"
    elif item.get("occlusion_suspected"):
        kind, label = "covered", "PLEASE UNCOVER YOUR FACE"
    elif item.get("retry"):
        kind, label = "checking", item.get("hint") or "Checking…"
    elif item.get("tiebreak_required"):
        kind, label = "card", "TAP CARD · LOOKALIKE CHECK" if item.get("confusable_pair") else "TAP CARD TO CONFIRM"
    elif item.get("success"):
        similarity = item.get("similarity")
        kind = "match"
        label = item.get("person_name") or "Match"
        if similarity is not None:
            label = f"{label} · {max(0, round(similarity * 100))}%"
    else:
        kind, label = "unknown", "UNKNOWN"
    return {
        "x": box["left"] / width, "y": box["top"] / height,
        "w": (box["right"] - box["left"]) / width, "h": (box["bottom"] - box["top"]) / height,
        "kind": kind, "label": label,
    }


def note_results(gate, answer):
    """The boxes of a gate's latest face check (its /api/identify answer).
    The picture checked is the same view the gate sends here (its zoom
    included), so a box's place as a share of the picture lines up."""
    if not isinstance(answer, dict) or not watched(gate):
        return
    size = answer.get("image_size") or {}
    faces = [face for face in (_face(item, size.get("width"), size.get("height"))
                               for item in answer.get("results") or []) if face]
    with _lock:
        _faces[gate] = {"at": time.monotonic(), "faces": faces}


def status(gates):
    """gate -> whether pictures are arriving, how long since the last one, and how many a second."""
    now = time.monotonic()
    out = {}
    with _lock:
        for gate in gates:
            entry = _frames.get(gate)
            if not entry or "at" not in entry:
                out[gate] = {"live": False, "age": None, "fps": None}
                continue
            age = now - entry["at"]
            arrivals = [t for t in entry["arrivals"] if now - t < 2.0]
            fps = (len(arrivals) - 1) / (arrivals[-1] - arrivals[0]) if len(arrivals) > 2 else None
            out[gate] = {"live": age < LIVE_FOR_SECONDS, "age": round(age, 1),
                         "fps": round(fps, 1) if fps and age < LIVE_FOR_SECONDS else None}
    return out


def _message(header, payload=b""):
    data = json.dumps(header).encode()
    return struct.pack(">I", len(data)) + data + payload


def stream(gates, stream_seconds=STREAM_SECONDS):
    """The Cameras page's live feed for these gates - a generator for a
    StreamingHttpResponse. Registers the page as a viewer (so the gates start
    sending) until the connection ends."""
    gates = list(dict.fromkeys(gates))[:MAX_GATES_PER_STREAM]
    viewer_id = uuid.uuid4().hex
    with _lock:
        _viewers[viewer_id] = {"gates": set(gates), "seen": time.monotonic()}
    sent = dict.fromkeys(gates, 0)
    started = last_status = time.monotonic()
    try:
        yield _message({"type": "status", "gates": status(gates)})
        while time.monotonic() - started < stream_seconds:
            with _lock:
                now = time.monotonic()
                _viewers[viewer_id]["seen"] = now
                fresh = [(gate, _frames[gate]) for gate in gates
                         if gate in _frames and _frames[gate].get("seq", 0) > sent[gate]]
                if not fresh and now - last_status < STATUS_EVERY_SECONDS:
                    _lock.wait(STATUS_EVERY_SECONDS - (now - last_status))
                    continue
                faces = {gate: _faces.get(gate) for gate, _entry in fresh}
                fresh = [(gate, entry["seq"], entry["jpeg"]) for gate, entry in fresh]
            for gate, seq, jpeg in fresh:
                sent[gate] = seq
                known = faces.get(gate)
                age = time.monotonic() - known["at"] if known else None
                yield _message({
                    "type": "frame", "gate": gate, "size": len(jpeg),
                    "faces": known["faces"] if known and age < FACES_FRESH_SECONDS else [],
                    "faces_age": round(age, 2) if age is not None else None,
                }, jpeg)
            if time.monotonic() - last_status >= STATUS_EVERY_SECONDS:
                last_status = time.monotonic()
                yield _message({"type": "status", "gates": status(gates)})
    finally:
        with _lock:
            _viewers.pop(viewer_id, None)
            still_watched = set().union(*(viewer["gates"] for viewer in _viewers.values()))
            # A gate nobody watches any more: its last picture isn't kept.
            for gate in gates:
                if gate not in still_watched:
                    _frames.pop(gate, None)
                    _faces.pop(gate, None)
