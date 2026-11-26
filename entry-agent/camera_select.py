"""Which camera the gate monitor uses: a plugged-in (USB) camera before the
laptop's built-in one.

Cameras are told apart with Windows' own device information: everything
built into a computer belongs to the computer's "machine container" (the
container ID {00000000-0000-0000-ffff-ffffffffffff}), while a plugged-in
camera gets its own. Software cameras (OBS, LSVCam, ...) and infrared
face-sign-in cameras are recognized by name; so is a built-in camera if the
Windows check can't run ("Integrated Camera").

The choice is remembered BY NAME (camera_choice.json in the data folder) -
camera numbers change whenever a camera is plugged in or out, names don't.
Order of preference, each time the gate monitor opens:
  1. a plugged-in camera (the remembered one first, if there are several)
  2. a camera that couldn't be told apart
  3. the built-in camera
  4. software cameras, then infrared cameras - they show nothing useful
     for a gate
"""

import json
import os
import re
import subprocess

from config import data_file

CHOICE_FILE = "camera_choice.json"
MACHINE_CONTAINER = "{00000000-0000-0000-ffff-ffffffffffff}"

EXTERNAL, UNKNOWN, BUILT_IN, SOFTWARE, INFRARED = "external", "unknown", "built_in", "software", "infrared"
PREFERENCE = (EXTERNAL, UNKNOWN, BUILT_IN, SOFTWARE, INFRARED)
LABELS = {
    EXTERNAL: "plugged-in camera",
    UNKNOWN: "camera",
    BUILT_IN: "built-in camera",
    SOFTWARE: "software camera",
    INFRARED: "infrared camera",
}

_BUILT_IN_WORDS = re.compile(r"integrated|built-?in|internal|\bfront\b|\brear\b", re.IGNORECASE)
_SOFTWARE_WORDS = re.compile(r"virtual|\bobs\b|vcam|droidcam|snap camera|manycam|xsplit|lsvcam|nvidia broadcast",
                             re.IGNORECASE)
_INFRARED_WORDS = re.compile(r"\bIR\b|infrared", re.IGNORECASE)
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def _key(name):
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _windows_cameras(timeout=8):
    """[(name key, is it built into the computer?)] for the camera devices
    Windows knows, or None if the check couldn't run."""
    if os.name != "nt":
        return None
    script = (
        "Get-PnpDevice -PresentOnly | Where-Object { $_.Class -in 'Camera','Image' } | ForEach-Object { "
        "$c = (Get-PnpDeviceProperty -InstanceId $_.InstanceId -KeyName DEVPKEY_Device_ContainerId "
        "-ErrorAction SilentlyContinue).Data; [pscustomobject]@{ n = $_.FriendlyName; c = \"$c\" } } "
        "| ConvertTo-Json -Compress"
    )
    try:
        result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                                capture_output=True, text=True, timeout=timeout, creationflags=_NO_WINDOW)
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout) if result.stdout.strip() else []
    except (subprocess.SubprocessError, OSError, ValueError):
        return None
    devices = data if isinstance(data, list) else [data]
    return [(_key(d.get("n")), (d.get("c") or "").strip().lower() == MACHINE_CONTAINER)
            for d in devices if isinstance(d, dict) and _key(d.get("n"))]


def _windows_match(name, windows_devices):
    """True (built in) / False (plugged in) for the Windows device with this
    name - matched loosely, since the two lists can spell it a bit
    differently - or None if there's no such device."""
    key = _key(name)
    if not key:
        return None
    for device_key, built_in in windows_devices:
        if device_key == key or (min(len(key), len(device_key)) >= 5 and (key in device_key or device_key in key)):
            return built_in
    return None


def classify(cameras, windows_devices=None):
    """[(index, name, kind)] for [(index, name)] - see the module docstring.
    windows_devices: for tests; normally looked up here."""
    if windows_devices is None:
        windows_devices = _windows_cameras()
    described = []
    for index, name in cameras:
        built_in = _windows_match(name, windows_devices) if windows_devices else None
        if _INFRARED_WORDS.search(name):
            kind = INFRARED
        elif _SOFTWARE_WORDS.search(name):
            kind = SOFTWARE
        elif built_in is not None:
            kind = BUILT_IN if built_in else EXTERNAL
        elif _BUILT_IN_WORDS.search(name):
            kind = BUILT_IN
        else:
            kind = UNKNOWN
        described.append((index, name, kind))
    return described


def _choices():
    try:
        with open(data_file(CHOICE_FILE), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


# The exit camera (lanes.py) is remembered beside the entrance one, under its
# own key; no key, or an empty name, means this gate has no exit camera.
# Two cameras of the same model have the same name, so which of them it was
# (its place among the same-named ones, in Windows' order) is kept too.
_KEYS = {"entry": "name", "exit": "exit_name"}
_PLACE_KEYS = {"entry": "place", "exit": "exit_place"}


def remembered_name(direction="entry"):
    return (_choices().get(_KEYS[direction]) or "").strip() or None


def remembered_place(direction="entry"):
    """Which of the cameras with the remembered name - 0 for the first (and
    for a choice saved before places were kept)."""
    try:
        return max(0, int(_choices().get(_PLACE_KEYS[direction]) or 0))
    except (TypeError, ValueError):
        return 0


def remember(name, direction="entry", place=0):
    """Remembers a camera someone picked (the gate monitor's "Cameras & card
    readers" window) - None for "no exit camera". `place`: which of the
    cameras with this name (place_of)."""
    data = _choices()
    data[_KEYS[direction]] = name or ""
    data[_PLACE_KEYS[direction]] = place if name else 0
    try:
        with open(data_file(CHOICE_FILE), "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    except OSError:
        pass  # a convenience - picking again next time still works


# "Use it as the exit camera?" (main.py asks when a second plugged-in camera
# shows up at a gate without an exit camera) - "Don't ask again" is kept here.
_NO_SUGGESTION_KEY = "exit_suggest_off"


def exit_suggestion_off():
    return bool(_choices().get(_NO_SUGGESTION_KEY))


def stop_suggesting_exit():
    data = _choices()
    data[_NO_SUGGESTION_KEY] = True
    try:
        with open(data_file(CHOICE_FILE), "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    except OSError:
        pass


def exit_candidate(described, entrance_index):
    """The camera to suggest as the exit camera: a plugged-in one (or one
    that couldn't be told apart) other than the entrance camera - never the
    laptop's own camera (it faces the guard), a software or an infrared
    camera. None when there's no such camera."""
    for kind in (EXTERNAL, UNKNOWN):
        for index, _name, camera_kind in described:
            if camera_kind == kind and index != entrance_index:
                return index
    return None


def place_of(described, index):
    """Which of the listed cameras with the same name as the one at `index`
    it is - 0 for the first (and for a camera whose name is its own)."""
    name = next((camera_name for i, camera_name, _kind in described if i == index), None)
    key = _key(name)
    return sum(1 for i, camera_name, _kind in described if _key(camera_name) == key and i < index) if key else 0


def find_by_name(described, name, place=0, avoid=None):
    """The index of the listed camera with this (remembered) name - the
    place-th of them when there are several of the same model - or None.
    `avoid`: a camera already in use (the entrance one, when looking for the
    exit's): with two of the same model it takes the other one instead,
    which also restores an exit camera saved before places were kept."""
    key = _key(name)
    if not key:
        return None
    matches = [index for index, camera_name, _kind in described if _key(camera_name) == key]
    if not matches:
        return None
    found = matches[place] if place < len(matches) else matches[0]
    if found == avoid:
        found = next((index for index in matches if index != avoid), found)
    return found


def same_device(before, index, after):
    """Where the camera listed at `index` in `before` (a classify() list) is
    in `after` - a fresh list taken later. Plugging a camera in can renumber
    the others, so it's found again by name - and, among cameras with the
    same name (two of the same webcam model), by its place in that order.
    None when it's no longer listed."""
    entry = next(((i, name) for i, name, _kind in before if i == index), None)
    if entry is None:
        return None
    key = _key(entry[1])
    place = sum(1 for i, name, _kind in before if _key(name) == key and i < index)
    matches = [i for i, name, _kind in after if _key(name) == key]
    return matches[place] if place < len(matches) else None


def choose(described, remembered=None, fallback_index=0, place=0):
    """The index of the camera to use from classify()'s list, or
    fallback_index when there are none. `place`: which of several cameras
    with the remembered name (two of the same model)."""
    if not described:
        return fallback_index
    target = find_by_name(described, remembered, place)

    def rank(item):
        index, _name, kind = item
        return PREFERENCE.index(kind), 0 if index == target else 1, index

    return min(described, key=rank)[0]
