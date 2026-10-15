"""TEMPORARY - times the camera scanner for testing; remove after the test.

Settings page (Advanced): "Speed test recording". While it's on, the gate
monitor adds each decided face's time to its box ("... · 843 ms"), shows the
average time per frame in the caption, and writes one row per decided face
to a CSV file in the data folder (speed-tests\\):

    trial, time, outcome, time_to_decide_ms, frames, avg_frame_ms
    1, 09:12:03.512, recognized, 843.2, 3, 281.4

- time_to_decide_ms: from the frame the face first got a box in (so from
  when it came within the distance limit) to the answer that decided it.
- frames: how many scanned frames that took.
- avg_frame_ms: those frames' average time, frame taken -> answer shown.
- outcome: recognized, unknown, spoof, or card tap asked.

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
FIELDS = ["trial", "time", "outcome", "time_to_decide_ms", "frames", "avg_frame_ms"]


def _outcome(item):
    """What the scan decided for this face in this frame, or None if it's
    still checking (or only asking the person to uncover their face)."""
    if item.get("retry") or item.get("occlusion_suspected"):
        return None
    if item.get("tiebreak"):
        return "card tap asked"
    if item.get("matched"):
        return "recognized"
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
        """Opens a new CSV when switched on, closes it when switched off."""
        if enabled and self._file is None:
            os.makedirs(self.folder, exist_ok=True)
            name = f"speed_test_{datetime.now():%Y%m%d_%H%M%S}.csv"
            self._file = open(os.path.join(self.folder, name), "w", newline="", encoding="utf-8")
            self._writer = csv.DictWriter(self._file, fieldnames=FIELDS)
            self._writer.writeheader()
            self._file.flush()
        elif not enabled and self._file is not None:
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

        live = [t for t in self._tracks if answered_at - t["last_seen"] <= TRACK_TIMEOUT_SECONDS]
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
                track = {"first_seen": captured_at, "frame_ms": [], "decided_ms": None}
                live.append(track)
            else:
                unclaimed.remove(track)
            track.update(x=x, y=y, width=width, last_seen=answered_at)
            if track["decided_ms"] is None:
                track["frame_ms"].append(frame_ms)
                outcome = _outcome(item)
                if outcome:
                    track["decided_ms"] = (answered_at - track["first_seen"]) * 1000
                    self._trials += 1
                    self._writer.writerow({
                        "trial": self._trials,
                        "time": datetime.now().strftime("%H:%M:%S.%f")[:-3],
                        "outcome": outcome,
                        "time_to_decide_ms": f"{track['decided_ms']:.1f}",
                        "frames": len(track["frame_ms"]),
                        "avg_frame_ms": f"{statistics.mean(track['frame_ms']):.1f}",
                    })
                    self._file.flush()  # nothing lost if the gate monitor is closed mid-test
            if track["decided_ms"] is not None:
                item["speed_ms"] = track["decided_ms"]
        self._tracks = live
        return f"speed test: {statistics.mean(self._recent_ms):.1f} ms per frame"
