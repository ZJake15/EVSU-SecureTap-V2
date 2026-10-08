"""This computer's own setup - shared by the launcher and its first-run setup
window (setup_wizard.py).

- The speed mode (Fast / Standard / Light). The speed test measures how fast
  this computer runs the gate's face check and picks one; it's saved in
  device_profile.json and handed to the backend and the gate monitor as
  environment overrides each time the launcher starts them - the same way the
  launcher already passes the gate name and direction, so the .env files a
  technician maintains are never rewritten for it.
- Creating the two .env files on a new computer, with fresh secrets.
- Bringing the gate's own settings (gate name, camera, card reader) over from
  an old copy - the backend's `import_securetap` brings the data itself.
"""

import ctypes
import json
import os
import re
import secrets
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent
BACKEND_DIR = ROOT / "backend"
ENTRY_AGENT_DIR = ROOT / "entry-agent"

# ---- the data folder ---------------------------------------------------------
#
# An installed copy lives in a program folder it shouldn't write to, and
# reinstalling or updating it must never touch the students' data - so
# everything the system writes goes to one data folder instead: the two .env
# files (as backend.env and entry-agent.env), the database, photos, the
# covered-face model, the card-tap queue and the launcher's own files. The
# installer marks an installed copy with INSTALLED_MARKER; SECURETAP_DATA_DIR
# overrides the location. Running from the code folder (no marker), every file
# stays where it always was.
INSTALLED_MARKER = ROOT / "securetap-installed.txt"


def _data_dir():
    if os.environ.get("SECURETAP_DATA_DIR"):
        return Path(os.environ["SECURETAP_DATA_DIR"])
    if INSTALLED_MARKER.exists():
        return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "EVSU SecureTap"
    return None


DATA_DIR = _data_dir()
if DATA_DIR is not None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # Inherited by every program the launcher starts - the backend
    # (settings.py) and the gate monitor (entry-agent/config.py) read it.
    os.environ["SECURETAP_DATA_DIR"] = str(DATA_DIR)


def _data_file(installed_name, source_path):
    return DATA_DIR / installed_name if DATA_DIR else source_path


BACKEND_ENV = _data_file("backend.env", BACKEND_DIR / ".env")
ENTRY_AGENT_ENV = _data_file("entry-agent.env", ENTRY_AGENT_DIR / ".env")
PROFILE_PATH = _data_file("device_profile.json", ROOT / "device_profile.json")
# The launcher's remembered gate/direction/guard name (launcher.py's
# SETTINGS_PATH) - an imported copy's choices are merged into it.
LAUNCHER_SETTINGS_PATH = _data_file("launcher_settings.json", ROOT / "launcher_settings.json")
# Written by the gate monitor (entry-agent/main.py, offline_queue.py), read
# by the launcher.
LAST_SESSION_PATH = _data_file("last_session.json", ENTRY_AGENT_DIR / "last_session.json")
OFFLINE_QUEUE_PATH = _data_file("offline_queue.db", ENTRY_AGENT_DIR / "offline_queue.db")

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


@dataclass(frozen=True)
class SpeedMode:
    label: str
    summary: str
    # Overrides for each program. Only what differs from Standard is listed,
    # so Standard leaves every .env value exactly as it is.
    backend: dict = field(default_factory=dict)
    entry_agent: dict = field(default_factory=dict)


MODES = {
    "fast": SpeedMode(
        "Fast", "The smoothest camera view and the quickest face checks. For a fast computer.",
        entry_agent={"VIDEO_FPS": "20", "SCAN_PAUSE_SECONDS": "0.1", "STUDENT_DISPLAY_FPS": "15"},
    ),
    "standard": SpeedMode("Standard", "The normal settings."),
    "light": SpeedMode(
        "Light",
        "Easier on the computer so nothing lags: a slightly less smooth camera view, and faces need to be "
        "within about 2-3 m of the camera.",
        backend={"GATE_SCAN_DET_SIZE": "384"},
        entry_agent={"UPLOAD_MAX_DIMENSION": "640", "VIDEO_FPS": "12", "SCAN_PAUSE_SECONDS": "0.35",
                     "STUDENT_DISPLAY_FPS": "6"},
    ),
}
DEFAULT_MODE = "standard"

# How the speed test picks a mode, from one face check's time (the middle
# value of 20, at the normal detection size) and the computer itself. The
# gate monitor's video, the face checks, the backend and the browser all
# share the processor, so a chip with few cores gets Light even when one face
# check alone is quick - the Intel N100 (4 cores) lands there, which is what
# testing on it called for.
FAST_BELOW_MS = 35
LIGHT_FROM_MS = 90
FEW_PROCESSORS = 4
LOW_MEMORY_GB = 6
SPEED_TEST_FRAMES = 20


def memory_gb():
    """This computer's total memory in GB, or None if it can't be read."""
    if os.name != "nt":
        return None

    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(MemoryStatus)
    try:
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
    except (AttributeError, OSError):
        return None
    return round(status.ullTotalPhys / 1024 ** 3, 1)


def run_speed_test(python):
    """Times the gate's face check on this computer (backend/manage.py
    benchmark_scan, with InsightFace's own test photo so it works before
    anyone is enrolled). Returns its measurements plus "memory_gb". Raises
    RuntimeError with the reason if it couldn't run. Takes about 10-40 s."""
    result = subprocess.run(
        [python, "manage.py", "benchmark_scan", "--sample", "--json", "--det-size", "480",
         "--frames", str(SPEED_TEST_FRAMES)],
        cwd=BACKEND_DIR, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=600, creationflags=_NO_WINDOW,
    )
    for line in reversed(result.stdout.splitlines()):
        if line.startswith("{"):
            measured = json.loads(line)
            measured["memory_gb"] = memory_gb()
            return measured
    reason = (result.stderr.strip().splitlines() or ["it stopped without a result"])[-1]
    raise RuntimeError(f"The speed test couldn't run: {reason}")


def recommend(measured):
    """(mode key, the reason in plain words) for a speed-test result."""
    ms = measured["ms_median"]
    processors = measured.get("processors") or os.cpu_count() or 1
    memory = measured.get("memory_gb")
    if ms >= LIGHT_FROM_MS:
        return "light", f"Each face check took {ms:.0f} ms, which is slow for a live camera."
    if processors <= FEW_PROCESSORS:
        return "light", (f"Each face check took {ms:.0f} ms, but this computer has only {processors} processor "
                         "cores to share between the camera view, the face checks and the dashboard.")
    if memory is not None and memory < LOW_MEMORY_GB:
        return "light", f"This computer has {memory:.0f} GB of memory, which is little for everything at once."
    if ms < FAST_BELOW_MS and processors >= 8 and (memory is None or memory >= 8):
        return "fast", f"Each face check took only {ms:.0f} ms and this computer has {processors} processor cores."
    return "standard", f"Each face check took {ms:.0f} ms - fine for the normal settings."


# ---- the saved profile -------------------------------------------------------

def load_profile():
    try:
        profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return profile if profile.get("mode") in MODES else None


def save_profile(mode, chosen_by, measured=None, recommended=None, reason=None):
    """chosen_by: "speed test" or "you". Keeps the last measurement when only
    the mode changes."""
    previous = load_profile() or {}
    profile = {
        "mode": mode,
        "chosen_by": chosen_by,
        "recommended": recommended or previous.get("recommended"),
        "reason": reason or previous.get("reason"),
        "measured": measured or previous.get("measured"),
        "tested_at": datetime.now().astimezone().isoformat(timespec="seconds") if measured
        else previous.get("tested_at"),
        "setup_done": True,
    }
    PROFILE_PATH.write_text(json.dumps(profile, indent=2), encoding="utf-8")
    return profile


def needs_setup():
    """True on a computer the first-run setup hasn't finished on."""
    profile = load_profile()
    return not (profile and profile.get("setup_done") and BACKEND_ENV.exists() and ENTRY_AGENT_ENV.exists())


def mode_of(profile):
    return profile["mode"] if profile else DEFAULT_MODE


def backend_env_overrides(profile):
    return dict(MODES[mode_of(profile)].backend)


def entry_agent_env_overrides(profile):
    return dict(MODES[mode_of(profile)].entry_agent)


def describe(profile):
    """One line for the launcher, e.g. "Light · picked by the speed test, Oct 08"."""
    if not profile:
        return f"{MODES[DEFAULT_MODE].label} · no speed test yet"
    label = MODES[profile["mode"]].label
    if profile.get("chosen_by") == "you":
        return f"{label} · chosen by you"
    try:
        when = datetime.fromisoformat(profile["tested_at"]).strftime("%b %d")
    except (KeyError, TypeError, ValueError):
        when = None
    return f"{label} · picked by the speed test" + (f", {when}" if when else "")


# ---- .env files ------------------------------------------------------------

def _read_env(path):
    # utf-8-sig: some Windows editors start the file with an invisible
    # byte-order mark, which would otherwise hide the first setting.
    return dotenv_values(path, encoding="utf-8-sig") if path.exists() else {}


def set_env_values(path, values):
    """Sets KEY=value lines in a .env file, keeping every other line (and
    its comments) as it is: replaces the KEY= line, or the commented-out
    "# KEY=" example line, or adds the line at the end."""
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    for key, value in values.items():
        active = re.compile(rf"^\s*{re.escape(key)}\s*=")
        example = re.compile(rf"^\s*#\s*{re.escape(key)}\s*=")
        line = f"{key}={value}"
        for pattern in (active, example):
            index = next((i for i, text in enumerate(lines) if pattern.match(text)), None)
            if index is not None:
                lines[index] = line
                break
        else:
            lines.append(line)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def create_env_files():
    """Creates backend/.env and entry-agent/.env from their .env.example
    templates on a new computer: a fresh secret key, and a fresh gate key
    written into both files so the gate monitor is accepted by the backend.
    Never touches a file that already exists. Returns the created files'
    names."""
    created = []
    if not BACKEND_ENV.exists():
        BACKEND_ENV.write_text((BACKEND_DIR / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")
        set_env_values(BACKEND_ENV, {
            "DJANGO_SECRET_KEY": secrets.token_urlsafe(50),
            "ENTRY_AGENT_SERVICE_TOKEN": secrets.token_urlsafe(32),
        })
        created.append(str(BACKEND_ENV))
    if not ENTRY_AGENT_ENV.exists():
        ENTRY_AGENT_ENV.write_text((ENTRY_AGENT_DIR / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")
        set_env_values(ENTRY_AGENT_ENV, {"SERVICE_TOKEN": _read_env(BACKEND_ENV).get("ENTRY_AGENT_SERVICE_TOKEN", "")})
        created.append(str(ENTRY_AGENT_ENV))
    return created


def gate_key_matches():
    """Whether the gate monitor's SERVICE_TOKEN is the backend's
    ENTRY_AGENT_SERVICE_TOKEN - if not, the backend turns every face check
    and card tap away."""
    backend = _read_env(BACKEND_ENV).get("ENTRY_AGENT_SERVICE_TOKEN") or ""
    return bool(backend) and backend == (_read_env(ENTRY_AGENT_ENV).get("SERVICE_TOKEN") or "")


def fix_gate_key():
    set_env_values(ENTRY_AGENT_ENV, {"SERVICE_TOKEN": _read_env(BACKEND_ENV).get("ENTRY_AGENT_SERVICE_TOKEN", "")})


# ---- an old copy's gate settings -------------------------------------------

# entry-agent/.env lines that describe this gate's hardware and identity.
GATE_ENV_KEYS = ("GATE_LOCATION", "DIRECTION", "CAMERA_INDEX", "CAMERA_EXPOSURE", "NFC_READER_USB_IDS",
                 "OFFICER_NAME")
LAUNCHER_KEYS = ("gate_location", "direction", "officer_name", "auto_launch_entry_agent")


def import_gate_settings(old_root):
    """Brings an old copy's gate settings into this one: the launcher's
    remembered gate, direction and guard name, and the entry-agent/.env
    lines for the gate name, camera and card reader. Security Officer
    accounts only see their own gate's records, matched by its exact name -
    which is why the gate name has to come along with the data. Returns plain
    descriptions of what was brought over."""
    old_root = Path(old_root)
    brought = []
    old_env = _read_env(old_root / "entry-agent" / ".env")
    values = {key: old_env[key] for key in GATE_ENV_KEYS if (old_env.get(key) or "").strip()}
    if values and ENTRY_AGENT_ENV.exists():
        set_env_values(ENTRY_AGENT_ENV, values)
        brought.append("the gate monitor's settings (" + ", ".join(sorted(values)) + ")")

    try:
        old_launcher = json.loads((old_root / "launcher_settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old_launcher = {}
    if not old_launcher.get("gate_location") and values.get("GATE_LOCATION"):
        old_launcher["gate_location"] = values["GATE_LOCATION"]
    if not old_launcher.get("direction") and values.get("DIRECTION"):
        old_launcher["direction"] = values["DIRECTION"]
    choices = {key: old_launcher[key] for key in LAUNCHER_KEYS if key in old_launcher}
    if choices:
        try:
            current = json.loads(LAUNCHER_SETTINGS_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            current = {}
        current.update(choices)
        LAUNCHER_SETTINGS_PATH.write_text(json.dumps(current, indent=2), encoding="utf-8")
        if choices.get("gate_location"):
            brought.append(f"the gate name \"{choices['gate_location']}\"")

    old_time_zone = (_read_env(old_root / "backend" / ".env").get("TIME_ZONE") or "").strip()
    if old_time_zone and old_time_zone != (_read_env(BACKEND_ENV).get("TIME_ZONE") or "").strip():
        set_env_values(BACKEND_ENV, {"TIME_ZONE": old_time_zone})
        brought.append(f"the time zone ({old_time_zone})")
    return brought


def list_cameras(python):
    """[(index, name), ...] of the cameras Windows reports - the same list
    the gate monitor's camera picker shows. Empty if none, or if the check
    itself failed."""
    try:
        result = subprocess.run(
            [python, "-c", "import json; from camera import list_available_cameras; "
                           "print(json.dumps(list_available_cameras()))"],
            cwd=ENTRY_AGENT_DIR, capture_output=True, text=True, timeout=30, creationflags=_NO_WINDOW,
        )
        return [tuple(item) for item in json.loads(result.stdout.strip().splitlines()[-1])]
    except (subprocess.SubprocessError, OSError, ValueError, IndexError):
        return []
