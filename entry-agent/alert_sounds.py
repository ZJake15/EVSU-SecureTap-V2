"""The gate monitor's alert sounds - a different one for each kind of event,
so a guard who isn't looking at the screen still knows what happened:

  unknown       two bright bell notes, high-low, twice
  spoof         harsh rising siren sweeps (the most urgent)
  covered       two soft low notes, once per person (a polite prompt)
  not_enrolled  two buzzy notes falling ("uh-oh") - refused, not enrolled

The sounds are the .wav files in sounds/ (remade by sounds/make_sounds.py).
Each one can be switched off in Settings > Alerts.
"""
import threading
import time
import wave
import winsound
from pathlib import Path

SOUNDS_DIR = Path(__file__).resolve().parent / "sounds"

# A more urgent sound cuts off a gentler one still playing; a gentler one
# never cuts off a more urgent one (it's skipped - the box and log still show it).
PRIORITY = {"spoof": 3, "unknown": 2, "not_enrolled": 2, "covered": 1}

# Only if a .wav file is missing or won't play: (frequency Hz, milliseconds) beeps.
FALLBACK_BEEPS = {
    "unknown": [(1200, 180), (900, 180)] * 2,
    "spoof": [(1600, 90), (800, 90)] * 4,
    "covered": [(520, 220)],
    "not_enrolled": [(470, 220), (350, 320)],
}


def _length(path):
    try:
        with wave.open(str(path), "rb") as sound:
            return sound.getnframes() / sound.getframerate()
    except (OSError, wave.Error, EOFError):
        return None


class AlertSounds:
    def __init__(self, folder=SOUNDS_DIR):
        self._files = {}
        self._lengths = {}
        for kind in PRIORITY:
            path = Path(folder) / f"{kind}.wav"
            length = _length(path) if path.is_file() else None
            if length:
                self._files[kind] = str(path)
                self._lengths[kind] = length
        self._busy_until = 0.0
        self._busy_priority = 0

    def play(self, kind):
        """Starts `kind`'s sound and returns at once - never blocks the screen.
        Returns False when it was skipped for a more urgent sound still playing."""
        priority = PRIORITY[kind]
        now = time.monotonic()
        if now < self._busy_until and priority < self._busy_priority:
            return False
        path = self._files.get(kind)
        if path:
            try:
                # SND_ASYNC: plays in the background; a new sound replaces the old.
                winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
                self._busy_until, self._busy_priority = now + self._lengths[kind], priority
                return True
            except RuntimeError:
                pass  # no audio device, or the file won't play - try plain beeps
        beeps = FALLBACK_BEEPS[kind]
        self._busy_until = now + sum(ms for _, ms in beeps) / 1000
        self._busy_priority = priority
        threading.Thread(target=_beep, args=(beeps,), daemon=True).start()
        return True


def _beep(beeps):
    try:
        for frequency, ms in beeps:
            winsound.Beep(frequency, ms)
    except RuntimeError:
        pass  # no audio device on this machine - never worth blocking the guard's UI over


class CoveredFaces:
    """Covered faces aren't logged, so they come back every frame with only a
    box - no event id to say "already announced". This keeps the soft sound
    to once per person: a covered box near one seen in the last few seconds
    is the same person still adjusting their mask; one that isn't is new."""

    FORGET_AFTER = 3.0  # seconds without a covered face there = they've gone

    def __init__(self):
        self._recent = []  # [(center_x, center_y, size, last_seen)]

    def new_arrivals(self, boxes, now=None):
        """How many of this frame's covered-face boxes are people not seen covered just before."""
        now = time.monotonic() if now is None else now
        self._recent = [entry for entry in self._recent if now - entry[3] <= self.FORGET_AFTER]
        arrivals = 0
        for box in boxes:
            cx, cy = (box["left"] + box["right"]) / 2, (box["top"] + box["bottom"]) / 2
            size = max(box["right"] - box["left"], box["bottom"] - box["top"], 1)
            match = next((i for i, (x, y, s, _) in enumerate(self._recent)
                          if abs(cx - x) <= max(s, size) and abs(cy - y) <= max(s, size)), None)
            if match is None:
                arrivals += 1
                self._recent.append((cx, cy, size, now))
            else:
                self._recent[match] = (cx, cy, size, now)
        return arrivals
