"""The gate monitor's alert sounds - a different one for each kind of event,
so a guard who isn't looking at the screen still knows what happened:

  unknown       two bright bell notes, high-low, twice
  spoof         rising siren sweeps, three times (the most urgent)
  covered       two soft low notes, once per person (a polite prompt)
  not_enrolled  two buzzy notes falling ("uh-oh") - refused, not enrolled

The sounds are the .wav files in sounds/ (remade by sounds/make_sounds.py).
Each one can be switched off in Settings > Alerts.

The gate monitor's sound button sets how loud they are (remembered on this
computer) and can mute them for a while - MUTE_MINUTES, then they come back
by themselves, so a muted gate is never forgotten. Windows' simple sound
player has no volume of its own, so a quieter copy of each sound is made
once per volume, in the temp folder.
"""
import tempfile
import threading
import time
import wave
import winsound
from pathlib import Path

SOUNDS_DIR = Path(__file__).resolve().parent / "sounds"
SCALED_DIR = Path(tempfile.gettempdir()) / "securetap-sounds"
MUTE_MINUTES = 15

# A more urgent sound cuts off a gentler one still playing; a gentler one
# never cuts off a more urgent one (it's skipped - the box and log still show it).
PRIORITY = {"spoof": 3, "unknown": 2, "not_enrolled": 2, "covered": 1}

# Only if a .wav file is missing or won't play: (frequency Hz, milliseconds) beeps.
FALLBACK_BEEPS = {
    "unknown": [(1200, 180), (900, 180)] * 2,
    "spoof": [(700, 150), (1000, 200)] * 3,
    "covered": [(520, 220)],
    "not_enrolled": [(470, 220), (350, 320)],
}


def _length(path):
    try:
        with wave.open(str(path), "rb") as sound:
            return sound.getnframes() / sound.getframerate()
    except (OSError, wave.Error, EOFError):
        return None


def _scaled_copy(path, volume, folder=SCALED_DIR):
    """A copy of the .wav at `path` at `volume` (0-1) of its loudness, made
    once and kept - or the original when it can't be made (an unusual
    format, a read-only temp folder)."""
    percent = round(volume * 100)
    target = Path(folder) / f"{Path(path).stem}-{percent}.wav"
    if target.is_file():
        return str(target)
    try:
        import numpy as np

        with wave.open(str(path), "rb") as source:
            params = source.getparams()
            frames = source.readframes(params.nframes)
        if params.sampwidth != 2:
            return str(path)
        samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) * (percent / 100)
        quieter = np.clip(samples, -32768, 32767).astype("<i2").tobytes()
        Path(folder).mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(".part")
        with wave.open(str(partial), "wb") as out:
            out.setparams(params)
            out.writeframes(quieter)
        partial.replace(target)
        return str(target)
    except (OSError, wave.Error, EOFError, ImportError, ValueError):
        return str(path)


class AlertSounds:
    def __init__(self, folder=SOUNDS_DIR, volume=1.0, scaled_folder=SCALED_DIR):
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
        self._scaled_folder = scaled_folder
        self._playing = {}  # kind -> the file played at the current volume
        self.volume = 1.0
        self.set_volume(volume)
        self._muted_until = 0.0

    # ---- volume and mute (the gate monitor's sound button) ----

    def set_volume(self, volume):
        """0-1, in steps of 5%. The quieter copies are made here, so the
        first alarm at a new volume isn't late."""
        try:
            volume = float(volume)
        except (TypeError, ValueError):
            volume = 1.0
        self.volume = min(1.0, max(0.0, round(volume * 20) / 20))
        self._playing = {}
        for kind, path in self._files.items():
            self._playing[kind] = path if self.volume >= 1.0 or self.volume <= 0 else _scaled_copy(
                path, self.volume, self._scaled_folder)

    def mute(self, minutes=MUTE_MINUTES):
        self._muted_until = time.monotonic() + minutes * 60

    def unmute(self):
        self._muted_until = 0.0

    def muted_seconds_left(self):
        """How long the sounds stay muted - 0 when they aren't."""
        return max(0.0, self._muted_until - time.monotonic())

    def is_silent(self):
        return self.muted_seconds_left() > 0 or self.volume <= 0

    def play(self, kind, preview=False):
        """Starts `kind`'s sound and returns at once - never blocks the screen.
        Returns False when it was skipped for a more urgent sound still
        playing, or while muted. `preview`: the guide's and the sound
        button's play buttons - heard even while muted, at the set volume
        (or full volume when that's 0, so the button does something)."""
        priority = PRIORITY[kind]
        now = time.monotonic()
        if not preview and self.is_silent():
            return False
        if now < self._busy_until and priority < self._busy_priority and not preview:
            return False
        path = self._playing.get(kind) if self.volume > 0 else self._files.get(kind)
        if path:
            try:
                # SND_ASYNC: plays in the background; a new sound replaces the old.
                winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
                if not preview:  # a play button never holds back a real alarm
                    self._busy_until, self._busy_priority = now + self._lengths[kind], priority
                return True
            except RuntimeError:
                pass  # no audio device, or the file won't play - try plain beeps
        beeps = FALLBACK_BEEPS[kind]
        if not preview:
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
