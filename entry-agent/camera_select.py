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


def remembered_name():
    try:
        with open(data_file(CHOICE_FILE), encoding="utf-8") as fh:
            return (json.load(fh).get("name") or "").strip() or None
    except (OSError, ValueError, AttributeError):
        return None


def remember(name):
    """Remembers a camera someone picked (setup window or the gate monitor's
    camera list)."""
    try:
        with open(data_file(CHOICE_FILE), "w", encoding="utf-8") as fh:
            json.dump({"name": name}, fh)
    except OSError:
        pass  # a convenience - picking again next time still works


def choose(described, remembered=None, fallback_index=0):
    """The index of the camera to use from classify()'s list, or
    fallback_index when there are none."""
    if not described:
        return fallback_index
    remembered = _key(remembered)

    def rank(item):
        index, name, kind = item
        return PREFERENCE.index(kind), 0 if remembered and _key(name) == remembered else 1, index

    return min(described, key=rank)[0]
