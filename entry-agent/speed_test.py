"""TEMPORARY - times the camera scanner for testing; remove after the test.

Settings page (Advanced): "Speed test recording". While it's on, the gate
monitor adds each decided face's time to its box ("... · 843 ms"), shows the
average time per frame in the caption, and writes one row per decided face
to a CSV file in the data folder (speed-tests\\):

    trial, time, outcome, time_to_decide_ms, frames, avg_frame_ms, distance_m, confidence_pct, best_confidence_pct
    1, 09:12:03.512, recognized, 843.2, 3, 281.4, 2.84, 78.6, 91.3

- outcome: recognized, unknown, spoof, or card tap asked - each counted
  only once the system has actually decided, not on a first frame that
  still needed more frames to agree. "recognized" means the entry was
  recorded (the result carries a log entry), not the first green frame.
- time / time_to_decide_ms: when it was decided, and how long from the
  frame the face first got a box in (so from when it came within the
  distance limit) to that decision.
- frames: how many scanned frames that took.
- avg_frame_ms: those frames' average time, frame taken -> answer shown.
- distance_m: the face's estimated distance from the camera in the frame
  that decided it (after Distance correction - see the backend's
  estimate_distance_m).
- confidence_pct: for a recognized face, its match percentage in the frame
  that decided it, to 0.1. best_confidence_pct: the highest match
  percentage the face's box showed while it was in view. Both blank for
  every other outcome.

A row is written when the face leaves the camera's view (unseen for
TRACK_TIMEOUT_SECONDS), or when the test is switched off or the gate
monitor closes - that's what lets it carry the best confidence of the
whole visit. A face that left before anything was decided isn't written.

Only times and outcomes - no names, ID numbers or photos. Times come from
time.perf_counter() (sub-millisecond on Windows; time.monotonic() only
ticks every ~15.6 ms there). Faces are followed from frame to frame by
where their box is.
"""

import csv
import math
import os
import statistics
from datetime import datetime

# A face not seen for this long has left; seeing it again starts a new timing.
TRACK_TIMEOUT_SECONDS = 1.5
# How many recent frames the caption's average covers.
CAPTION_FRAMES = 10
FIELDS = [
    "trial", "time", "outcome", "time_to_decide_ms", "frames", "avg_frame_ms",
    "distance_m", "confidence_pct", "best_confidence_pct",
]


def _number(value, pattern, scale=1):
    """A CSV cell: the number formatted, or blank when there isn't one."""
    return "" if value is None else pattern.format(value * scale)


def _outcome(item):
    """What the scan has decided for this face, or None while it's still
    checking (or only asking the person to uncover their face). A green box
    that hasn't been recorded yet (log_id still empty - waiting for enough
    frames to agree) is still checking."""
    if item.get("retry") or item.get("occlusion_suspected"):
        return None
    if item.get("tiebreak"):
        return "card tap asked"
    if item.get("matched"):
        return "recognized" if item.get("log_id") is not None else None
    if item.get("spoof_suspected"):
        return "spoof"
    return "unknown"


class SpeedTest:
    def __init__(self, folder):
        self.folder = folder
        self._file = None
        self._writer = None
        self._tracks = []
        self._recent_ms = []
        self._trials = 0

    def set_enabled(self, enabled):
        """Opens a new CSV when switched on; when switched off, writes the
        faces still in view and closes it."""
        if enabled and self._file is None:
            os.makedirs(self.folder, exist_ok=True)
            name = f"speed_test_{datetime.now():%Y%m%d_%H%M%S}.csv"
            self._file = open(os.path.join(self.folder, name), "w", newline="", encoding="utf-8")
            self._writer = csv.DictWriter(self._file, fieldnames=FIELDS)
            self._writer.writeheader()
            self._file.flush()
        elif not enabled and self._file is not None:
            for track in self._tracks:
                self._write(track)
            self._file.close()
            self._file = self._writer = None
            self._tracks, self._recent_ms, self._trials = [], [], 0

    def record(self, captured_at, answered_at, recognitions):
        """One scanned frame: captured_at/answered_at are time.perf_counter()
        readings (frame taken, answer ready to show). Adds "speed_ms" to each
        decided face's dict, and returns the caption text (None when off)."""
        if self._file is None:
            return None
        frame_ms = (answered_at - captured_at) * 1000
        self._recent_ms = (self._recent_ms + [frame_ms])[-CAPTION_FRAMES:]

        live = []
        for track in self._tracks:
            if answered_at - track["last_seen"] <= TRACK_TIMEOUT_SECONDS:
                live.append(track)
            else:
                self._write(track)  # left the view
        unclaimed = list(live)
        for item in recognitions:
            box = item["box"]
            x, y = (box["left"] + box["right"]) / 2, (box["top"] + box["bottom"]) / 2
            width = box["right"] - box["left"]
            # The nearest face from the last frames, if it's within a box
            # width - someone walking moves less than that in one frame.
            nearby = [t for t in unclaimed if math.hypot(t["x"] - x, t["y"] - y) < max(width, t["width"])]
            track = min(nearby, key=lambda t: math.hypot(t["x"] - x, t["y"] - y), default=None)
            if track is None:
                track = {"first_seen": captured_at, "frame_ms": [], "row": None, "best_similarity": None}
                live.append(track)
            else:
                unclaimed.remove(track)
            track.update(x=x, y=y, width=width, last_seen=answered_at)
            similarity = item.get("similarity") if item.get("matched") else None
            if similarity is not None:
                track["best_similarity"] = max(similarity, track["best_similarity"] or similarity)
            if track["row"] is None:
                track["frame_ms"].append(frame_ms)
                outcome = _outcome(item)
                if outcome:
                    self._trials += 1
                    decided_ms = (answered_at - track["first_seen"]) * 1000
                    track["row"] = {
                        "trial": self._trials,
                        "time": datetime.now().strftime("%H:%M:%S.%f")[:-3],
                        "outcome": outcome,
                        "time_to_decide_ms": f"{decided_ms:.1f}",
                        "frames": len(track["frame_ms"]),
                        "avg_frame_ms": f"{statistics.mean(track['frame_ms']):.1f}",
                        "distance_m": _number(item.get("speed_test_distance_m"), "{:.2f}"),
                        "confidence_pct": _number(similarity if outcome == "recognized" else None, "{:.1f}", 100),
                    }
                    track["decided_ms"] = decided_ms
            if track["row"] is not None:
                item["speed_ms"] = track["decided_ms"]
        self._tracks = live
        return f"speed test: {statistics.mean(self._recent_ms):.1f} ms per frame"

    def _write(self, track):
        """Writes a face's row once it's gone - skipped if nothing was decided."""
        row = track["row"]
        if row is None:
            return
        best = track["best_similarity"] if row["outcome"] == "recognized" else None
        self._writer.writerow({**row, "best_confidence_pct": _number(best, "{:.1f}", 100)})
        self._file.flush()  # nothing lost if the gate monitor is closed mid-test
