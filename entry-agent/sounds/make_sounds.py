"""Makes the gate monitor's alert sounds (the .wav files beside this script).

Run it again after changing a sound:  python entry-agent/sounds/make_sounds.py

Standard library only, so the files can always be remade. Each sound has its
own pitch direction and timbre, so a guard can tell them apart without looking:

  unknown.wav       two bright bell notes, high-low, twice   (attention)
  spoof.wav         harsh rising siren sweeps, four times    (most urgent)
  covered.wav       two soft low notes, rising, quiet        (a polite prompt)
  not_enrolled.wav  two buzzy notes falling, "uh-oh"         (refused)
"""
import math
import struct
import wave
from pathlib import Path

RATE = 22050
HERE = Path(__file__).resolve().parent


def silence(seconds):
    return [0.0] * int(RATE * seconds)


def note(freq, seconds, harmonics=((1, 1.0),), attack=0.01, decay=None, release=0.03):
    """One note. `harmonics` is (multiple, strength) pairs - the timbre.
    decay=None holds the note steady (buzzer); a number fades it out like a
    struck bell, with that time constant in seconds."""
    count = int(RATE * seconds)
    out = []
    for i in range(count):
        t = i / RATE
        value = sum(strength * math.sin(2 * math.pi * freq * multiple * t) for multiple, strength in harmonics)
        level = min(1.0, t / attack) if attack else 1.0
        if decay:
            level *= math.exp(-t / decay)
        level *= min(1.0, (seconds - t) / release) if release else 1.0
        out.append(value * level)
    return out


def sweep(start, end, seconds, harmonics):
    """A siren sweep from `start` to `end` Hz, phase kept continuous."""
    count = int(RATE * seconds)
    out, phase = [], 0.0
    for i in range(count):
        t = i / RATE
        freq = start + (end - start) * (t / seconds)
        phase += 2 * math.pi * freq / RATE
        level = min(1.0, t / 0.005) * min(1.0, (seconds - t) / 0.01)
        out.append(level * sum(strength * math.sin(phase * multiple) for multiple, strength in harmonics))
    return out


BELL = ((1, 1.0), (2, 0.35), (3, 0.12), (4.2, 0.05))
HARSH = ((1, 1.0), (3, 1 / 3), (5, 1 / 5), (7, 1 / 7))  # square-ish
SOFT = ((1, 1.0), (2, 0.08))
BUZZ = tuple((n, 1 / n) for n in range(1, 8))  # sawtooth-ish


def unknown():
    hi = note(1175, 0.2, BELL, attack=0.004, decay=0.12)
    lo = note(880, 0.2, BELL, attack=0.004, decay=0.12)
    return hi + lo + hi + note(880, 0.45, BELL, attack=0.004, decay=0.16), 0.75


def spoof():
    one = sweep(750, 1650, 0.22, HARSH) + silence(0.04)
    return one * 4, 0.85


def covered():
    return (note(392, 0.32, SOFT, attack=0.02, decay=0.22) + silence(0.03)
            + note(523, 0.55, SOFT, attack=0.02, decay=0.3)), 0.32


def not_enrolled():
    return (note(466, 0.26, BUZZ, attack=0.015, release=0.04) + silence(0.05)
            + note(349, 0.42, BUZZ, attack=0.015, release=0.08)), 0.55


def write(name, samples, peak):
    loudest = max(abs(s) for s in samples) or 1.0
    frames = b"".join(struct.pack("<h", round(s / loudest * peak * 32767)) for s in samples)
    with wave.open(str(HERE / name), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(frames)
    print(f"{name}: {len(samples) / RATE:.2f} s")


if __name__ == "__main__":
    for maker in (unknown, spoof, covered, not_enrolled):
        samples, peak = maker()
        write(f"{maker.__name__}.wav", samples, peak)
