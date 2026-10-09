"""EVSU SecureTap - single launcher for the whole system.

One window replaces the three terminals the README used to ask for: it starts
the Django backend itself, then lets whoever's at the machine pick what they
actually want - the web dashboard or the gate's entry-agent - without typing
anything.

The backend starts automatically rather than sitting behind a third button,
because it isn't a choice: both the dashboard and the entry-agent are useless
without it. The only real decision is which front end to open, so that's the
only decision this window asks for.

Every child process it spawns is tracked and killed on quit (via taskkill /T,
so npm's node child goes with it), and their merged output is kept in a rolling
buffer that the "Show log" panel reveals - otherwise a backend that dies
because its port is taken would just look like a button that does nothing.

Run it with the repo's venv:  .venv\\Scripts\\python.exe launcher.py
or double-click SecureTap.bat, which does the same thing.
"""

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from collections import deque
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
import requests
from dotenv import dotenv_values
from PIL import Image

import device_setup

ROOT = Path(__file__).resolve().parent

# The design system lives with the entry-agent's UI. Imported rather than
# duplicated so the launcher and the gate monitor can't drift into looking
# like two different products - they're one system with one look.
sys.path.insert(0, str(ROOT / "entry-agent"))
from ui import (  # noqa: E402
    BRASS,
    CANVAS,
    CAUTION,
    CAUTION_TINT,
    COND_BOLD,
    DANGER,
    DANGER_TINT,
    FONT,
    FONT_MONO,
    ICON_FONT,
    ICON_FONT_BOLD,
    ICON_PATH,
    INK,
    INK_400,
    INK_600,
    LINE,
    MAROON,
    MAROON_DEEP,
    PROMPT,
    SEMI_HEAVY,
    SURFACE,
    VERIFIED,
    WIDE_BLACK,
    _apply_icon,
    _icon,
    fit_to_screen,
    set_app_user_model_id,
)

BACKEND_DIR = ROOT / "backend"
DASHBOARD_DIR = ROOT / "dashboard"
ENTRY_AGENT_DIR = ROOT / "entry-agent"

HEALTH_URL = "http://127.0.0.1:8000/api/health"
# The oldest backend this launcher works with - logs/views.py HealthView.API_VERSION.
BACKEND_API_VERSION = 2
# The backend serves the dashboard itself, from its ready-made build
# (backend/securetap_project/spa_views.py) - no separate dashboard server.
DASHBOARD_URL = "http://localhost:8000/"

HEALTH_POLL_MS = 2000
# How often the launcher re-runs the data clean-up while it stays open.
CLEANUP_INTERVAL_SECONDS = 24 * 60 * 60
# "Back up data" (backend/manage.py backup_data): the row's hint turns to a
# caution colour once the last backup is older than this. The password's
# minimum length is configuration/backup_file.py's MIN_PASSWORD_LENGTH.
BACKUP_REMINDER_DAYS = 7
BACKUP_MIN_PASSWORD = 10
BACKUP_RESULT_MARKER = "SECURETAP_RESULT"
LOG_MAX_LINES = 500
LOG_REFRESH_MS = 700

# Everything below the header lives in a content column capped at this width
# and centered - past this, the window just grows background, not widgets.
CONTENT_MAX_WIDTH = 960
# Side gutter (logical px) when the window is narrower than the cap.
CONTENT_GUTTER = 26
# The two choice cards sit side by side once each can be at least this wide
# (plus the gap between them) - the redesign's auto-fit minmax(360px, 1fr).
CARD_MIN_WIDTH = 360
CARD_GAP = 16
DEFAULT_WINDOW_WIDTH = 560
DEFAULT_WINDOW_HEIGHT = 760
# At or above this logical window width (i.e. maximized on a desktop) the
# header grows and the sections get more breathing room, per the redesign's
# maximized frame.
LARGE_LAYOUT_MIN_WIDTH = 900

# The entry-agent takes a real few seconds to appear: importing cv2, opening
# the webcam through DirectShow, then building the Tk window. Rather than
# guessing at a duration, main.py prints this marker on the line just before it
# hands off to its main loop, and the launcher stops the spinner when it sees
# it - so the animation ends when the window is actually up, not when a timer
# says it should be. Keep in sync with entry-agent/main.py.
ENTRY_AGENT_READY_MARKER = "SECURETAP_ENTRY_AGENT_READY"

# How often a "starting" card checks whether it has waited too long - the
# visible motion is the card's indeterminate progress bar.
SPINNER_INTERVAL_MS = 200
# If a service never signals ready, stop spinning and say so - a spinner that
# never stops is worse than an error message.
STARTUP_TIMEOUT_MS = 45000

# Descriptions for the two choice cards.
DASHBOARD_DESCRIPTION = "Web app — register users, live monitoring, logs, reports"
ENTRY_AGENT_DESCRIPTION = "Gate monitor — camera face recognition + NFC card reader"

# Keeps a child's own console window from flashing up alongside ours. The
# entry-agent's Tk windows are unaffected - this suppresses the console, not
# the GUI.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

# How each service reads in the status bar: word, color, icon.
SERVICE_STATES = {
    "ready": ("Ready", VERIFIED, "check-circle"),
    "running": ("Running", VERIFIED, "check-circle"),
    "starting": ("Starting…", PROMPT, "circle-notch"),
    # Ink, not grey - "not running" is a plain fact the guard needs to read,
    # and the lighter grey was too faint on the white bar.
    "off": ("Not running", INK, "circle"),
    "failed": ("Failed", DANGER, "x-circle"),
    "stopped": ("Stopped", DANGER, "x-circle"),
}

# The strip under a choice card while its service starts, runs or fails:
# text color, background, icon. "slow" is the 45-second timeout - not a
# confirmed failure, so caution rather than danger.
CARD_STATES = {
    "starting": (PROMPT, SURFACE, "circle-notch"),
    "running": (VERIFIED, SURFACE, "check-circle"),
    "failed": (DANGER, DANGER_TINT, "x-circle"),
    "slow": (CAUTION, CAUTION_TINT, "warning"),
}


def venv_python():
    """The repo's shared virtualenv interpreter. Everything (Django, the
    entry-agent, InsightFace) is installed into the one .venv at the repo root,
    so both child processes use it. Falls back to whatever is running this
    launcher, which is the right answer when someone already activated a venv
    by hand. An installed copy has no .venv - its own Python ships in
    python/ (see installer/build.py)."""
    for candidate in (ROOT / ".venv" / "Scripts" / ("python.exe" if os.name == "nt" else "python"),
                      ROOT / "python" / "python.exe"):
        if candidate.exists():
            return str(candidate)
    # The installed shortcut runs this launcher with pythonw.exe (no console
    # window). Its children get python.exe from the same folder instead, so
    # their output still reaches the log panel through the pipe.
    executable = Path(sys.executable)
    console = executable.with_name("python.exe")
    return str(console) if executable.name.lower() == "pythonw.exe" and console.exists() else sys.executable


def npm_command():
    """npm on Windows is npm.cmd - a batch file, not an .exe - so plain
    which("npm") can miss it. Returns None when Node isn't installed at all,
    which is worth saying out loud rather than failing with a bare
    FileNotFoundError."""
    return shutil.which("npm.cmd") or shutil.which("npm")


def _dashboard_build_exists():
    return (DASHBOARD_DIR / "dist" / "index.html").exists()


def _dashboard_build_is_current():
    """True when dashboard/dist (from `npm run build`) exists and is newer
    than every dashboard source file - so the backend is serving the latest
    dashboard. An out-of-date build gets rebuilt first (see _open_dashboard),
    so a dashboard code change can't silently go missing."""
    if not _dashboard_build_exists():
        return False
    built_at = (DASHBOARD_DIR / "dist" / "index.html").stat().st_mtime
    sources = [DASHBOARD_DIR / name for name in ("index.html", "package.json", "vite.config.js", "tailwind.config.js")]
    sources += list((DASHBOARD_DIR / "src").rglob("*"))
    return all(path.stat().st_mtime <= built_at for path in sources if path.is_file())


def backend_identity(timeout=1.0):
    """Which backend answers on port 8000: "ours" (this version, or newer),
    "old" (an older SecureTap - its /api/health has no api_version, e.g. the
    old copy still running on a computer the new app was installed on), or
    None (nothing). The launcher never uses an old one: the dashboard and
    the gate monitor would quietly run on the old system and its old data."""
    try:
        response = requests.get(HEALTH_URL, timeout=timeout)
    except requests.RequestException:
        return None
    try:
        data = response.json()
    except ValueError:
        return "old"
    version = data.get("api_version") if isinstance(data, dict) else None
    return "ours" if isinstance(version, int) and version >= BACKEND_API_VERSION else "old"


def service_responds(url, timeout=1.0):
    """Whether something is already serving this URL.

    Checked before starting anything, because plenty of people already have
    `runserver` open in a terminal. Spawning a second one
    fails on "port already in use" and dies - but the health probe would still
    get an answer from the *other* instance, so the launcher would show a
    cheerful green light next to a child process that's already dead. Adopting
    what's running instead is both honest and what the user wanted anyway.
    """
    try:
        requests.get(url, timeout=timeout)
        return True
    except requests.RequestException:
        return False


# ---- launcher-remembered settings (gate, direction, guard name) -----------
#
# A small local JSON file rather than rewriting entry-agent/.env - .env is
# meant to be a one-time deployment config a technician edits by hand
# (SERVICE_TOKEN, API_BASE_URL, ...), not something this GUI silently
# overwrites underneath them on every launch. These three specific values are
# passed to the entry-agent subprocess as environment variable OVERRIDES
# instead (see _open_entry_agent below) - entry-agent/config.py already reads
# GATE_LOCATION/DIRECTION/OFFICER_NAME from the environment with its own
# .env-file defaults, so this needs zero entry-agent code changes: an
# environment variable already wins over python-dotenv's load_dotenv() when
# both are set, because load_dotenv() doesn't override existing values.
SETTINGS_PATH = device_setup.LAUNCHER_SETTINGS_PATH
DEFAULT_GATE_LOCATION = "Main Gate"
DEFAULT_DIRECTION = "entry"

# Where main.py's on_close() writes a small summary of the session just
# ended (entries/exits/unknown/spoof/occlusion counts, gate, direction, a
# timestamp) - see entry-agent/main.py's _write_last_session_summary. Read
# back here so the launcher can show "last session" before the entry-agent
# is even opened again.
LAST_SESSION_PATH = device_setup.LAST_SESSION_PATH

# The NFC reader's own sqlite-backed retry queue (entry-agent/offline_queue.py)
# - read directly with a plain sqlite3 connection rather than importing
# OfflineQueue itself, since that class also wants a live ApiClient just to
# construct, which isn't needed for a read-only pending-count peek.
OFFLINE_QUEUE_DB_PATH = device_setup.OFFLINE_QUEUE_PATH

# USB vendor:product IDs of the NFC card readers the pre-flight check looks
# for. The gate's reader identifies to Windows as a generic HID keyboard
# (that's how it "types" a scanned card's ID), so there's no NFC-specific API
# to ask "is a reader plugged in" - matching known VID:PIDs in Windows' own
# device list is the only practical check, and it only knows the readers
# listed here:
#   072F:2200 - a genuine ACS ACR122U.
#   FFFF:0035 - the unbranded keyboard-emulation reader actually in use at
#               this gate. Low-cost readers sold under the ACR122U name often
#               report a generic ID like this rather than ACS's own.
# Override per machine with NFC_READER_USB_IDS in entry-agent/.env
# (comma-separated VID:PID pairs) - see _nfc_reader_usb_ids.
DEFAULT_NFC_READER_USB_IDS = ("072F:2200", "FFFF:0035")


def _load_settings():
    if not SETTINGS_PATH.exists():
        return {}
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}  # a corrupt/unreadable settings file just means "use defaults"


def _save_settings(settings):
    try:
        SETTINGS_PATH.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    except OSError:
        pass  # best-effort - a remembered gate/direction is a convenience, not critical state


def _read_app_version():
    """entry-agent/.env's APP_VERSION is the one place this is already
    configured (see entry-agent/config.py) - read here too so the launcher
    and the gate monitor always show the same version rather than the
    launcher guessing at its own separate constant."""
    env_path = device_setup.ENTRY_AGENT_ENV
    if not env_path.exists():
        return "v1.0"
    try:
        return dotenv_values(env_path).get("APP_VERSION") or "v1.0"
    except Exception:
        return "v1.0"


def _offline_queue_pending_count():
    """Best-effort peek at how many NFC taps are still waiting to sync -
    returns 0 if the db doesn't exist yet (nothing has ever queued) or can't
    be read, rather than raising and blocking the launcher from opening."""
    if not OFFLINE_QUEUE_DB_PATH.exists():
        return 0
    try:
        conn = sqlite3.connect(str(OFFLINE_QUEUE_DB_PATH))
        try:
            row = conn.execute("SELECT COUNT(*) FROM pending_taps").fetchone()
            return row[0] if row else 0
        finally:
            conn.close()
    except sqlite3.Error:
        return 0


def _read_last_session_summary():
    if not LAST_SESSION_PATH.exists():
        return None
    try:
        return json.loads(LAST_SESSION_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _format_last_session_line(summary):
    """The detail half of the "Last session" notice (the bold "Last session"
    label is drawn separately)."""
    if not summary:
        return None
    try:
        ended = datetime.fromisoformat(summary["ended_at"]).astimezone().strftime("%b %d, %I:%M %p")
    except (KeyError, ValueError):
        ended = "an unknown time"
    return (
        f"{summary.get('gate_location', '?')} ({summary.get('direction', '?')}): "
        f"{summary.get('entries', 0)} entries, {summary.get('exits', 0)} exits, "
        f"{summary.get('unknown', 0)} unknown — ended {ended}"
    )


_USB_ID_PATTERN = re.compile(r"^\s*([0-9A-Fa-f]{4})\s*:\s*([0-9A-Fa-f]{4})\s*$")


def _nfc_reader_usb_ids():
    """The VID:PID pairs the pre-flight check accepts as "a card reader is
    plugged in": NFC_READER_USB_IDS from entry-agent/.env if it's set (and has
    at least one well-formed pair), otherwise DEFAULT_NFC_READER_USB_IDS.
    Malformed entries are skipped rather than failing the check."""
    configured = None
    env_path = device_setup.ENTRY_AGENT_ENV
    if env_path.exists():
        try:
            configured = dotenv_values(env_path).get("NFC_READER_USB_IDS")
        except Exception:
            configured = None
    ids = []
    for entry in (configured or "").split(","):
        match = _USB_ID_PATTERN.match(entry)
        if match:
            ids.append(f"{match.group(1).upper()}:{match.group(2).upper()}")
    return tuple(ids) or DEFAULT_NFC_READER_USB_IDS


def _detect_nfc_reader(usb_ids, timeout=4.0):
    """Best-effort check that one of the known card readers (usb_ids, as
    VID:PID pairs) is plugged in, via Windows' own PnP device list. Returns
    True/False, or None when the check itself couldn't run (not Windows,
    powershell missing/timed out) - None is deliberately NOT treated as
    "absent" by callers, since a failed check saying "no reader found" would
    be actively misleading during a demo."""
    if os.name != "nt" or not usb_ids:
        return None
    # usb_ids only ever holds validated hex pairs (see _nfc_reader_usb_ids),
    # so they're safe to put straight into the PowerShell pattern.
    pattern = "|".join(f"VID_{vid}&PID_{pid}" for vid, pid in (usb_id.split(":") for usb_id in usb_ids))
    try:
        result = subprocess.run(
            [
                "powershell", "-NoProfile", "-NonInteractive", "-Command",
                "(Get-PnpDevice -PresentOnly | Where-Object "
                f"{{ $_.InstanceId -match '{pattern}' }}).Count",
            ],
            capture_output=True, text=True, timeout=timeout, creationflags=_NO_WINDOW,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip() not in ("", "0")
    except (subprocess.SubprocessError, OSError):
        return None


class ManagedProcess:
    """One child process the launcher owns: how to start it, whether it's
    still alive, and how to stop it and everything it spawned."""

    def __init__(self, label, on_output=None):
        self.label = label
        self.process = None
        self._on_output = on_output

    def is_running(self):
        return self.process is not None and self.process.poll() is None

    def start(self, args, cwd, env_overrides=None):
        if self.is_running():
            return True
        env = None
        if env_overrides:
            # Layered on top of the launcher's own environment (which the
            # child inherits by default when env=None) rather than replacing
            # it outright - dropping PATH/PYTHONHOME etc. here would break
            # the child in ways that have nothing to do with what's actually
            # being overridden (gate/direction/officer name).
            env = {**os.environ, **env_overrides}
        self.process = subprocess.Popen(
            args,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # one stream to read; Django logs to stderr
            stdin=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
            creationflags=_NO_WINDOW,
            env=env,
        )
        threading.Thread(target=self._pump_output, args=(self.process,), daemon=True).start()
        return True

    def _pump_output(self, process):
        """Drains the child's output on its own thread. Without this the pipe
        fills, the child blocks on its next write, and the whole service
        silently wedges - a 64KB buffer is only a few dozen Django request
        log lines."""
        for line in process.stdout:
            if self._on_output:
                self._on_output(f"[{self.label}] {line.rstrip()}")
        process.stdout.close()

    def stop(self):
        if not self.is_running():
            return
        if os.name == "nt":
            # /T takes the children too. npm spawns node as a child, so
            # terminating npm alone would leave the dev server holding its port.
            subprocess.run(
                ["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                capture_output=True,
                creationflags=_NO_WINDOW,
            )
        else:
            self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()


def _hairline(parent, **pack_kwargs):
    """A 1px LINE rule. A plain tk.Frame, since a CTkFrame this thin draws
    nothing at all."""
    rule = tk.Frame(parent, bg=LINE, height=1)
    rule.pack(fill="x", **pack_kwargs)
    return rule


def _icon_label(parent, name, size, color, bold=True, **kwargs):
    family = ICON_FONT_BOLD if bold else ICON_FONT
    return ctk.CTkLabel(parent, text=_icon(name, bold), font=(family or FONT, size), text_color=color, **kwargs)


class LauncherWindow:
    def __init__(self):
        self._log = deque(maxlen=LOG_MAX_LINES)
        self._log_dirty = False
        self._log_lock = threading.Lock()
        self._browser_opened = False
        self._backend_ok = False
        # "External" = already running when we got here, so it's not ours to
        # start and not ours to kill on quit.
        self._backend_external = False
        # True while the database update that runs before the backend starts
        # is going (see _start_backend), and if that update failed.
        self._backend_preparing = False
        self._backend_update_failed = False
        # An older SecureTap's backend is on port 8000 (see _block_old_backend).
        self._backend_blocked = False
        # Dashboard button: clicked before the backend was up (open it once it
        # is), and whether its last rebuild failed - see _open_dashboard.
        self._open_when_backend_ready = False
        self._dashboard_failed = False
        self._spinners = {}
        # True while the Entry Agent's pre-flight checks run in the background,
        # so a second click can't start a second round of them.
        self._preflight_running = False
        # The daily data clean-up (backend/manage.py purge_old_data) - see
        # _maybe_run_cleanup.
        self._cleanup_running = False
        self._last_cleanup_at = None
        # Set by the entry-agent's output-reader thread, acted on by the Tk
        # main thread in _flush_log - same rule as the Vite URL above.
        self._entry_agent_ready = False
        # "Did we start it and see it run", so the card's state strip can be
        # cleared once it stops instead of describing a window that's closed.
        self._entry_agent_started = False
        self._dashboard_started = False

        self.settings = _load_settings()
        self.app_version = _read_app_version()
        # This computer's speed mode (device_setup.py) - passed to the backend
        # and the gate monitor when they start.
        self.profile = device_setup.load_profile()
        self._speed_test_running = False
        self._backup_running = False
        # Set by "Run setup again" - main() reopens the setup window after
        # this window closes.
        self.setup_requested = False

        self.backend = ManagedProcess("backend", self._append_log)
        self.dashboard = ManagedProcess("dashboard", self._handle_dashboard_output)
        self.entry_agent = ManagedProcess("entry-agent", self._handle_entry_agent_output)

        self.root = ctk.CTk()
        self.root.title("EVSU SecureTap")
        self.root.configure(fg_color=CANVAS)
        fit_to_screen(self.root, DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT, 520, 680)
        self.root.protocol("WM_DELETE_WINDOW", self._handle_quit)
        _apply_icon(self.root)
        self._scale = ctk.ScalingTracker.get_widget_scaling(self.root)

        # Layout state, recomputed on every resize by _apply_responsive_layout.
        self._side_padding = None
        self._large = None
        self._cards_side_by_side = None
        self._wrap_labels = []  # (label, px reserved beside it) - re-wrapped to the content width

        self._build_header()
        self._build_footer()  # packed before the body so the body takes what's left
        self._build_body()

        self.root.bind("<Configure>", self._on_root_configure)
        # Paints the correct layout immediately, before the first real
        # <Configure> event fires, using the width just requested above.
        self._apply_responsive_layout(DEFAULT_WINDOW_WIDTH)

        self._start_backend()
        self.root.after(500, self._poll_health)
        self.root.after(LOG_REFRESH_MS, self._flush_log)

        if self.settings.get("auto_launch_entry_agent"):
            # Deferred rather than called immediately - lets the window
            # actually paint first, so "auto-launch" doesn't look like the
            # launcher hanging on a blank frame.
            self.root.after(1200, self._open_entry_agent)

    # ---- layout -----------------------------------------------------------

    def _build_header(self):
        """Maroon header with the brass rule: seal, EVSU / SecureTap wordmark
        and "Choose what to open". Grows when the window is maximized."""
        self.header = ctk.CTkFrame(self.root, fg_color=MAROON_DEEP, corner_radius=0, height=76)
        self.header.pack(fill="x")
        self.header.pack_propagate(False)
        ctk.CTkFrame(self.root, fg_color=BRASS, corner_radius=0, height=3).pack(fill="x")

        self.header_inner = ctk.CTkFrame(self.header, fg_color="transparent")
        self.header_inner.pack(fill="both", expand=True, padx=CONTENT_GUTTER)
        self._seal_image = None
        try:
            seal = Image.open(ICON_PATH).convert("RGBA")
            self._seal_image = ctk.CTkImage(light_image=seal, dark_image=seal, size=(48, 48))
            ctk.CTkLabel(self.header_inner, image=self._seal_image, text="").pack(side="left", padx=(0, 20))
        except Exception:
            pass  # a missing seal asset shouldn't stop the launcher from opening

        text = ctk.CTkFrame(self.header_inner, fg_color="transparent")
        text.pack(side="left")
        wordmark = ctk.CTkFrame(text, fg_color="transparent")
        wordmark.pack(anchor="w")
        ctk.CTkLabel(wordmark, text="EVSU", font=(WIDE_BLACK, 28), text_color="white", height=30).pack(
            side="left", anchor="s", padx=(0, 10)
        )
        ctk.CTkLabel(wordmark, text="SecureTap", font=(SEMI_HEAVY, 28), text_color="white", height=30).pack(
            side="left", anchor="s"
        )
        ctk.CTkLabel(text, text="Choose what to open", font=(FONT, 16), text_color="white", height=22).pack(
            anchor="w", pady=(4, 0)
        )

    def _build_body(self):
        """The scrollable middle: services bar, the two choice cards, the
        read-once notices, and the settings + log card. Scrolls rather than
        clipping when Entry Agent settings or the log are open in a short
        window."""
        self.body = ctk.CTkScrollableFrame(
            self.root, fg_color=CANVAS, corner_radius=0,
            scrollbar_button_color=LINE, scrollbar_button_hover_color=INK_400,
        )
        self.body.pack(fill="both", expand=True)
        self.content = ctk.CTkFrame(self.body, fg_color="transparent")
        self.content.pack(fill="x", padx=CONTENT_GUTTER, pady=(16, 16))

        self._sections = []
        self._build_services()
        self._build_cards()
        self._build_notices()
        self._build_settings_card()

    def _section(self):
        """A full-width block in the content column; the gap between blocks
        widens in the maximized layout (see _apply_responsive_layout)."""
        frame = ctk.CTkFrame(self.content, fg_color="transparent")
        frame.pack(fill="x", pady=(0, 12))
        self._sections.append(frame)
        return frame

    def _build_services(self):
        """One white bar with a cell per service - its name in condensed caps
        and its state as an icon + word in the state's color. The name and
        the state share one line when the cell is wide enough, and stack
        (state under name) when it isn't - see _fit_services."""
        section = self._section()
        bar = ctk.CTkFrame(section, fg_color=SURFACE, corner_radius=8, border_width=1, border_color=LINE)
        bar.pack(fill="x")
        self.status_widgets = {}
        self._service_cells = []
        services = (("backend", "BACKEND"), ("dashboard", "DASHBOARD"), ("entry-agent", "ENTRY AGENT"))
        for index, (key, name) in enumerate(services):
            if index:
                tk.Frame(bar, bg=LINE, width=1).grid(row=0, column=index * 2 - 1, sticky="ns", pady=1)
            bar.grid_columnconfigure(index * 2, weight=1, uniform="service")
            cell = ctk.CTkFrame(bar, fg_color="transparent", height=40)
            cell.grid(row=0, column=index * 2, sticky="ew", padx=(12, 12), pady=1)
            cell.pack_propagate(False)
            name_label = ctk.CTkLabel(cell, text=name, font=(COND_BOLD, 12), text_color=INK_600, height=18)
            # Icon and word live in one frame so they always move together -
            # packed separately, a narrow cell squeezed the icon over the name.
            status = ctk.CTkFrame(cell, fg_color="transparent")
            icon = _icon_label(status, "circle", 16, INK, height=18)
            icon.pack(side="left", padx=(0, 4))
            word = ctk.CTkLabel(status, text="", font=(FONT, 13, "bold"), text_color=INK, height=18)
            word.pack(side="left")
            self.status_widgets[key] = (icon, word)
            self._service_cells.append((cell, name_label, status))

        self._services_stacked = None
        self._layout_services(stacked=False)
        for key in self.status_widgets:
            self._set_status(key, "off")
        # On the cells, not the bar - the bar's <Configure> fires before the
        # grid has resized the cells inside it, so it would measure stale widths.
        for cell, _name, _status in self._service_cells:
            cell.bind("<Configure>", lambda _event: self._fit_services(), add="+")

        self.status_message = ctk.CTkLabel(
            section, text="", font=(FONT, 14), text_color=INK_600, anchor="w", justify="left"
        )
        self._wrap_labels.append((self.status_message, 4))

    def _fit_services(self):
        """Stacks every service cell's state under its name when the
        narrowest cell can't fit both on one line (the default 560px window
        with "ENTRY AGENT" + "Not running"), and puts them back side by side
        once there's room - measured from the labels' real widths, so it
        stays right as the state words change."""
        cells = self._service_cells
        width = min(cell.winfo_width() for cell, _name, _status in cells)
        if width <= 1:
            return  # not laid out yet
        needed = max(name.winfo_reqwidth() + status.winfo_reqwidth() for _cell, name, status in cells)
        self._layout_services(stacked=width < needed + round(12 * self._scale))

    def _layout_services(self, stacked):
        if stacked == self._services_stacked:
            return
        self._services_stacked = stacked
        for cell, name, status in self._service_cells:
            name.pack_forget()
            status.pack_forget()
            if stacked:
                cell.configure(height=52)
                name.pack(anchor="w", pady=(7, 0))
                status.pack(anchor="w")
            else:
                cell.configure(height=40)
                name.pack(side="left")
                status.pack(side="right")

    def _build_cards(self):
        section = self._section()
        self.cards_frame = ctk.CTkFrame(section, fg_color="transparent")
        self.cards_frame.pack(fill="x")
        self._cards = {}
        self._cards["dashboard"] = self._choice_card(
            "Dashboard", DASHBOARD_DESCRIPTION, "squares-four", self._open_dashboard
        )
        self._cards["entry-agent"] = self._choice_card(
            "Entry Agent", ENTRY_AGENT_DESCRIPTION, "video-camera", self._open_entry_agent
        )

    def _choice_card(self, title, description, icon_name, command):
        """A clickable white card: maroon icon tile, title, description and a
        caret, plus a state strip underneath (starting / running / failed)
        that only shows while there's something to say. A CTkFrame with a
        click binding on every part rather than a CTkButton, since a button
        can't hold a title, a wrapped description and an icon tile."""
        card = ctk.CTkFrame(self.cards_frame, fg_color=SURFACE, corner_radius=8, border_width=1,
                            border_color=LINE, cursor="hand2")
        top = ctk.CTkFrame(card, fg_color="transparent", cursor="hand2")
        top.pack(fill="x", padx=16, pady=12)

        tile = ctk.CTkFrame(top, fg_color=MAROON, corner_radius=8, width=44, height=44, cursor="hand2")
        tile.pack(side="left", anchor="n")
        tile.pack_propagate(False)
        tile_icon = _icon_label(tile, icon_name, 24, "white", bold=False, cursor="hand2")
        tile_icon.place(relx=0.5, rely=0.5, anchor="center")

        caret = _icon_label(top, "caret-right", 20, MAROON, cursor="hand2")
        caret.pack(side="right", anchor="n", pady=(10, 0))

        text = ctk.CTkFrame(top, fg_color="transparent", cursor="hand2")
        text.pack(side="left", fill="x", expand=True, padx=(16, 10))
        title_label = ctk.CTkLabel(text, text=title, font=(SEMI_HEAVY, 20), text_color=INK, anchor="w",
                                   height=24, cursor="hand2")
        title_label.pack(fill="x")
        description_label = ctk.CTkLabel(text, text=description, font=(FONT, 14), text_color=INK_600, anchor="w",
                                         justify="left", wraplength=240, cursor="hand2")
        description_label.pack(fill="x", pady=(4, 0))
        # Re-wrap to the text column's real width on every resize. <Configure>
        # reports real pixels; CTk's wraplength is logical.
        text.bind(
            "<Configure>",
            lambda event: description_label.configure(wraplength=max(120, event.width / self._scale - 4)),
            add="+",
        )

        # State strip under a hairline. Square, but inset from the card's
        # edge (see _set_card_state) far enough to clear its rounded corners.
        rule = tk.Frame(card, bg=LINE, height=1, cursor="hand2")
        strip = ctk.CTkFrame(card, fg_color=SURFACE, corner_radius=0, height=36, cursor="hand2")
        strip.pack_propagate(False)
        strip_content = ctk.CTkFrame(strip, fg_color=SURFACE, corner_radius=0, cursor="hand2")
        strip_content.pack(fill="x", padx=14, pady=(8, 0))
        state_row = ctk.CTkFrame(strip_content, fg_color=SURFACE, corner_radius=0, cursor="hand2")
        state_row.pack(fill="x")
        state_icon = _icon_label(state_row, "circle-notch", 16, PROMPT, cursor="hand2")
        state_icon.pack(side="left", padx=(0, 6))
        state_text = ctk.CTkLabel(state_row, text="", font=(FONT, 14, "bold"), text_color=PROMPT, anchor="w",
                                  cursor="hand2")
        state_text.pack(side="left", fill="x", expand=True)
        progress = ctk.CTkProgressBar(strip_content, height=4, corner_radius=2, mode="indeterminate",
                                      fg_color=LINE, progress_color=PROMPT)

        parts = {
            "card": card, "rule": rule, "strip": strip, "content": strip_content, "row": state_row,
            "icon": state_icon, "text": state_text, "progress": progress, "state": None,
        }

        clickable = (card, top, tile, tile_icon, caret, text, title_label, description_label, rule, strip,
                     strip_content, state_row, state_icon, state_text)

        # Hover marks the whole card with a maroon border - an outline change
        # rather than a fill change, since CTk only pushes a new fill color
        # one level down and the card's nested frames would lag behind.
        def on_enter(_event):
            card.configure(border_color=MAROON)

        def on_leave(_event):
            card.configure(border_color=LINE)

        for widget in clickable:
            widget.bind("<Button-1>", lambda _event: command())
            widget.bind("<Enter>", on_enter)
            widget.bind("<Leave>", on_leave)
        return parts

    def _set_card_state(self, key, kind, text=None):
        """Shows (or, with kind=None, hides) the strip under a choice card -
        see CARD_STATES."""
        parts = self._cards[key]
        if kind is None:
            parts["progress"].stop()
            parts["progress"].pack_forget()
            parts["strip"].pack_forget()
            parts["rule"].pack_forget()
            parts["state"] = None
            return
        color, background, icon_name = CARD_STATES[kind]
        for name in ("strip", "content", "row"):
            parts[name].configure(fg_color=background)
        parts["icon"].configure(text=_icon(icon_name), text_color=color, fg_color=background)
        parts["text"].configure(text=text or "", text_color=color, fg_color=background)
        if kind == "starting":
            parts["progress"].pack(fill="x", pady=(6, 0))
            parts["progress"].start()
            parts["strip"].configure(height=46)
        else:
            parts["progress"].stop()
            parts["progress"].pack_forget()
            parts["strip"].configure(height=36)
        if parts["state"] is None:
            parts["rule"].pack(fill="x", padx=1)
            # Inset 3px so the strip's square corners can't paint over the
            # card's rounded bottom corners or its 1px border.
            parts["strip"].pack(fill="x", padx=3, pady=(0, 3))
        parts["state"] = kind

    def _relayout_cards(self, side_by_side):
        dashboard, entry_agent = self._cards["dashboard"]["card"], self._cards["entry-agent"]["card"]
        dashboard.grid_forget()
        entry_agent.grid_forget()
        if side_by_side:
            self.cards_frame.grid_columnconfigure(0, weight=1, uniform="card")
            self.cards_frame.grid_columnconfigure(1, weight=1, uniform="card")
            dashboard.grid(row=0, column=0, sticky="nsew", padx=(0, CARD_GAP // 2))
            entry_agent.grid(row=0, column=1, sticky="nsew", padx=(CARD_GAP // 2, 0))
        else:
            self.cards_frame.grid_columnconfigure(0, weight=1, uniform="")
            self.cards_frame.grid_columnconfigure(1, weight=0, uniform="")
            dashboard.grid(row=0, column=0, sticky="ew", pady=(0, CARD_GAP))
            entry_agent.grid(row=1, column=0, sticky="ew")

    def _build_notices(self):
        """Startup-only, read-once information: what happened last time the
        gate monitor ran, and whether any NFC taps are still waiting to sync.
        Both come from files the entry-agent itself wrote, not from anything
        this launcher is tracking live - only shown if there's actually
        something to say, so a normal launch doesn't grow an empty box."""
        last_session_line = _format_last_session_line(_read_last_session_summary())
        pending = _offline_queue_pending_count()
        if not last_session_line and not pending:
            return
        section = self._section()

        if last_session_line:
            row = ctk.CTkFrame(section, fg_color="transparent")
            row.pack(fill="x", padx=2)
            _icon_label(row, "clock-counter-clockwise", 18, INK_600, bold=False).pack(side="left", anchor="n")
            text = ctk.CTkFrame(row, fg_color="transparent")
            text.pack(side="left", fill="x", expand=True, padx=(10, 0))
            ctk.CTkLabel(text, text="Last session", font=(FONT, 14, "bold"), text_color=INK, anchor="w",
                         height=20).pack(fill="x")
            detail = ctk.CTkLabel(text, text=last_session_line, font=(FONT, 14), text_color=INK, anchor="w",
                                  justify="left")
            detail.pack(fill="x")
            self._wrap_labels.append((detail, 34))

        if pending:
            noun = "tap hasn't" if pending == 1 else "taps haven't"
            box = ctk.CTkFrame(section, fg_color=CAUTION_TINT, corner_radius=3)
            box.pack(fill="x", pady=(10 if last_session_line else 0, 0))
            inner = ctk.CTkFrame(box, fg_color="transparent")
            inner.pack(fill="x", padx=16, pady=10)
            _icon_label(inner, "cloud-slash", 18, CAUTION).pack(side="left", anchor="n")
            text = ctk.CTkFrame(inner, fg_color="transparent")
            text.pack(side="left", fill="x", expand=True, padx=(10, 0))
            headline = ctk.CTkLabel(text, text=f"{pending} NFC {noun} synced to the server yet",
                                    font=(FONT, 14, "bold"), text_color=INK, anchor="w", justify="left")
            headline.pack(fill="x")
            detail = ctk.CTkLabel(
                text, text="They'll retry automatically once the entry agent is running and online.",
                font=(FONT, 14), text_color=INK, anchor="w", justify="left",
            )
            detail.pack(fill="x")
            self._wrap_labels.extend(((headline, 66), (detail, 66)))

    def _build_settings_card(self):
        """Entry Agent settings and the log, as two expandable rows of one
        white card. Settings = gate, direction and guard display name for the
        NEXT entry-agent launch - collapsed by default since most launches
        reuse what was picked last time. Deliberately not inside the Entry
        Agent card: that card's whole surface is a click target for opening
        it, and a text field inside it would eat or trigger those clicks."""
        section = self._section()
        card = ctk.CTkFrame(section, fg_color=SURFACE, corner_radius=8, border_width=1, border_color=LINE)
        card.pack(fill="x")

        # -- settings row ----------------------------------------------------
        self.settings_caret, self.settings_summary = self._accordion_row(
            card, "Entry Agent settings", self._toggle_settings_panel
        )
        self.settings_rule = tk.Frame(card, bg=LINE, height=1)
        self.settings_panel = ctk.CTkFrame(card, fg_color="transparent")
        self._settings_panel_visible = False

        grid = ctk.CTkFrame(self.settings_panel, fg_color="transparent")
        grid.pack(fill="x", padx=16, pady=16)
        grid.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(grid, text="Gate", font=(FONT, 14, "bold"), text_color=INK, anchor="w", height=20).grid(
            row=0, column=0, sticky="w"
        )
        self.gate_entry = self._entry(grid)
        self.gate_entry.insert(0, self.settings.get("gate_location", DEFAULT_GATE_LOCATION))
        self.gate_entry.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        self.gate_entry.bind("<FocusOut>", lambda _e: self._save_current_settings())

        ctk.CTkLabel(grid, text="Direction", font=(FONT, 14, "bold"), text_color=INK, anchor="w", height=20).grid(
            row=0, column=1, sticky="w", padx=(16, 0)
        )
        self._direction = "exit" if self.settings.get("direction", DEFAULT_DIRECTION) == "exit" else "entry"
        self.direction_selector = self._direction_toggle(grid)
        self.direction_selector.grid(row=1, column=1, sticky="w", padx=(16, 0), pady=(6, 0))

        guard_label = ctk.CTkFrame(grid, fg_color="transparent")
        guard_label.grid(row=2, column=0, columnspan=2, sticky="w", pady=(16, 0))
        ctk.CTkLabel(guard_label, text="Guard name", font=(FONT, 14, "bold"), text_color=INK, height=20).pack(
            side="left"
        )
        ctk.CTkLabel(guard_label, text="(optional)", font=(FONT, 14), text_color=INK_600, height=20).pack(
            side="left", padx=(4, 0)
        )
        self.guard_name_entry = self._entry(grid, placeholder="Shown on the gate monitor screen only")
        # Only pre-filled when there's a saved name - inserting even an empty
        # string makes CTkEntry drop its placeholder text.
        if self.settings.get("officer_name"):
            self.guard_name_entry.insert(0, self.settings["officer_name"])
        self.guard_name_entry.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self.guard_name_entry.bind("<FocusOut>", lambda _e: self._save_current_settings())

        self.auto_launch_var = tk.BooleanVar(value=bool(self.settings.get("auto_launch_entry_agent", False)))
        ctk.CTkCheckBox(
            grid, text="Automatically open the gate monitor when this launcher starts",
            font=(FONT, 14), text_color=INK, variable=self.auto_launch_var,
            fg_color=MAROON, hover_color=MAROON_DEEP, border_color=INK_400, corner_radius=3,
            checkbox_width=20, checkbox_height=20, command=self._save_current_settings,
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(16, 0))
        self._update_settings_summary()

        # -- speed mode row ------------------------------------------------------
        self.speed_rule = tk.Frame(card, bg=LINE, height=1)
        self.speed_rule.pack(fill="x", padx=1)
        self.speed_caret, self.speed_summary = self._accordion_row(card, "Speed mode", self._toggle_speed_panel)
        self.speed_panel_rule = tk.Frame(card, bg=LINE, height=1)
        self.speed_panel = ctk.CTkFrame(card, fg_color="transparent")
        self._speed_panel_visible = False
        speed = ctk.CTkFrame(self.speed_panel, fg_color="transparent")
        speed.pack(fill="x", padx=16, pady=16)
        self.speed_detail = ctk.CTkLabel(speed, text="", font=(FONT, 14), text_color=INK, anchor="w", justify="left")
        self.speed_detail.pack(fill="x")
        self._wrap_labels.append((self.speed_detail, 34))
        self.mode_selector = self._mode_toggle(speed)
        self.mode_selector.pack(anchor="w", pady=(12, 0))
        buttons = ctk.CTkFrame(speed, fg_color="transparent")
        buttons.pack(fill="x", pady=(14, 0))
        self.speed_test_button = self._outline_button(buttons, "Run the speed test again", self._run_speed_test)
        self.speed_test_button.pack(side="left")
        self._outline_button(buttons, "Run setup again", self._request_setup).pack(side="left", padx=(10, 0))
        self.speed_note = ctk.CTkLabel(speed, text="", font=(FONT, 13), text_color=INK_600, anchor="w",
                                       justify="left")
        self.speed_note.pack(fill="x", pady=(10, 0))
        self._wrap_labels.append((self.speed_note, 34))
        self._render_speed()

        # -- backup row --------------------------------------------------------
        self.backup_rule = tk.Frame(card, bg=LINE, height=1)
        self.backup_rule.pack(fill="x", padx=1)
        self.backup_caret, self.backup_summary = self._accordion_row(card, "Back up data", self._toggle_backup_panel)
        self.backup_panel_rule = tk.Frame(card, bg=LINE, height=1)
        self.backup_panel = ctk.CTkFrame(card, fg_color="transparent")
        self._backup_panel_visible = False
        backup = ctk.CTkFrame(self.backup_panel, fg_color="transparent")
        backup.pack(fill="x", padx=16, pady=16)
        intro = ctk.CTkLabel(
            backup, text="Saves everyone registered - their faces and photos - with the entry records, accounts and "
                         "settings into one file, locked with a password you choose. Put it on a USB drive and keep "
                         "it somewhere safe: if this computer breaks or is lost, setup can bring everything back "
                         "from it.",
            font=(FONT, 14), text_color=INK, anchor="w", justify="left",
        )
        intro.pack(fill="x")
        self._wrap_labels.append((intro, 34))
        buttons = ctk.CTkFrame(backup, fg_color="transparent")
        buttons.pack(fill="x", pady=(14, 0))
        self.backup_button = self._outline_button(buttons, "Back up now…", self._back_up)
        self.backup_button.pack(side="left")
        self.backup_note = ctk.CTkLabel(backup, text="", font=(FONT, 13), text_color=INK_600, anchor="w",
                                        justify="left")
        self.backup_note.pack(fill="x", pady=(10, 0))
        self._wrap_labels.append((self.backup_note, 34))
        restore = ctk.CTkLabel(
            backup, text="To bring a backup back: Speed mode > Run setup again > Your data > Choose backup file.",
            font=(FONT, 13), text_color=INK_600, anchor="w", justify="left",
        )
        restore.pack(fill="x", pady=(6, 0))
        self._wrap_labels.append((restore, 34))
        self._render_backup()

        # -- log row -----------------------------------------------------------
        self.log_rule = tk.Frame(card, bg=LINE, height=1)
        self.log_rule.pack(fill="x", padx=1)
        self.log_caret, self.log_hint = self._accordion_row(card, "Show log", self._toggle_log)
        self.log_box = ctk.CTkTextbox(
            card, font=(FONT_MONO, 12), fg_color=CANVAS, text_color=INK_600, corner_radius=3,
            border_width=0, wrap="none", height=220,
        )
        self._log_visible = False

    def _accordion_row(self, parent, title, command):
        """A 44px clickable row: caret, bold title, and a mono hint on the
        right. Returns (caret label, hint label)."""
        row = ctk.CTkFrame(parent, fg_color="transparent", height=44, cursor="hand2")
        row.pack(fill="x", padx=16, pady=1)
        row.pack_propagate(False)
        caret = _icon_label(row, "caret-right", 14, INK, cursor="hand2")
        caret.pack(side="left", padx=(0, 10))
        title_label = ctk.CTkLabel(row, text=title, font=(FONT, 14, "bold"), text_color=INK, cursor="hand2")
        title_label.pack(side="left")
        hint = ctk.CTkLabel(row, text="", font=(FONT_MONO, 12), text_color=INK_600, cursor="hand2")
        hint.pack(side="right")
        for widget in (row, caret, title_label, hint):
            widget.bind("<Button-1>", lambda _event: command())
        return caret, hint

    @staticmethod
    def _entry(parent, placeholder=None):
        return ctk.CTkEntry(
            parent, height=44, corner_radius=3, border_width=1, border_color=LINE, fg_color=SURFACE,
            text_color=INK, font=(FONT, 14), placeholder_text=placeholder, placeholder_text_color=INK_400,
        )

    def _direction_toggle(self, parent):
        """Entry | Exit segmented control - the selected half solid ink, as in
        the dashboard's own toggles. Built from frames rather than
        CTkSegmentedButton, which uses one text color for both states."""
        frame = ctk.CTkFrame(parent, fg_color=SURFACE, corner_radius=3, border_width=1, border_color=LINE,
                             width=200, height=44)
        frame.grid_propagate(False)
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure((0, 2), weight=1, uniform="direction")
        self._direction_halves = {}
        options = (("entry", "Entry", "sign-in"), ("exit", "Exit", "sign-out"))
        for column, (value, label, icon_name) in enumerate(options):
            if column:
                tk.Frame(frame, bg=LINE, width=1).grid(row=0, column=1, sticky="ns", pady=1)
            half = ctk.CTkFrame(frame, fg_color=SURFACE, corner_radius=0, cursor="hand2")
            half.grid(row=0, column=column * 2, sticky="nsew", padx=1, pady=1)
            inner = ctk.CTkFrame(half, fg_color="transparent", cursor="hand2")
            inner.place(relx=0.5, rely=0.5, anchor="center")
            icon = _icon_label(inner, icon_name, 16, INK_600, cursor="hand2")
            icon.pack(side="left", padx=(0, 6))
            text = ctk.CTkLabel(inner, text=label, font=(FONT, 14, "bold"), text_color=INK_600, cursor="hand2")
            text.pack(side="left")
            for widget in (half, inner, icon, text):
                widget.bind("<Button-1>", lambda _event, v=value: self._set_direction(v))
            self._direction_halves[value] = (half, inner, icon, text)
        self._render_direction()
        return frame

    def _set_direction(self, value):
        self._direction = value
        self._render_direction()
        self._save_current_settings()

    def _render_direction(self):
        for value, (half, inner, icon, text) in self._direction_halves.items():
            selected = value == self._direction
            background = INK if selected else SURFACE
            color = "white" if selected else INK_600
            half.configure(fg_color=background)
            inner.configure(fg_color=background)
            icon.configure(text_color=color, fg_color=background)
            text.configure(text_color=color, fg_color=background)

    def _build_footer(self):
        footer = ctk.CTkFrame(self.root, fg_color=SURFACE, corner_radius=0)
        footer.pack(side="bottom", fill="x")
        tk.Frame(self.root, bg=LINE, height=1).pack(side="bottom", fill="x")
        self.footer_inner = ctk.CTkFrame(footer, fg_color="transparent")
        self.footer_inner.pack(fill="x", padx=CONTENT_GUTTER, pady=10)
        ctk.CTkLabel(
            self.footer_inner, text=f"Quitting stops everything this window started. · {self.app_version}",
            font=(FONT, 12), text_color=INK_600,
        ).pack(side="left")

        # Outlined in danger red - quitting stops every service at once.
        quit_button = ctk.CTkFrame(self.footer_inner, fg_color=SURFACE, corner_radius=8, border_width=1,
                                   border_color=DANGER, cursor="hand2")
        quit_button.pack(side="right")
        inner = ctk.CTkFrame(quit_button, fg_color="transparent", cursor="hand2")
        inner.pack(padx=20, pady=10)
        icon = _icon_label(inner, "power", 16, DANGER, cursor="hand2", height=20)
        icon.pack(side="left", padx=(0, 6))
        text = ctk.CTkLabel(inner, text="Quit", font=(FONT, 14, "bold"), text_color=DANGER, cursor="hand2", height=20)
        text.pack(side="left")

        def paint(color):
            quit_button.configure(fg_color=color)
            inner.configure(fg_color=color)
            icon.configure(fg_color=color)
            text.configure(fg_color=color)

        for widget in (quit_button, inner, icon, text):
            widget.bind("<Button-1>", lambda _event: self._handle_quit())
            widget.bind("<Enter>", lambda _event: paint(DANGER_TINT))
            widget.bind("<Leave>", lambda _event: paint(SURFACE))

    # ---- responsive layout ------------------------------------------------

    def _on_root_configure(self, event):
        # <Configure> also fires for every child widget's own size/position
        # changes, not just the root window's.
        if event.widget is not self.root:
            return
        self._apply_responsive_layout(event.width / self._scale)

    def _apply_responsive_layout(self, window_width):
        """window_width is logical px. Caps the content column at
        CONTENT_MAX_WIDTH and centers it (header, body and footer share the
        same side padding so their edges line up), picks stacked vs side-by-
        side cards, and switches between the compact and maximized header."""
        side = max(CONTENT_GUTTER, round((window_width - CONTENT_MAX_WIDTH) / 2))
        if side != self._side_padding:
            self._side_padding = side
            self.header_inner.pack_configure(padx=side)
            self.footer_inner.pack_configure(padx=side)
            self.content.pack_configure(padx=side)
        # The body's scrollbar takes ~16px of the width on the right.
        content_width = window_width - 2 * side - 16

        side_by_side = content_width >= 2 * CARD_MIN_WIDTH + CARD_GAP
        if side_by_side != self._cards_side_by_side:
            self._cards_side_by_side = side_by_side
            self._relayout_cards(side_by_side)

        large = window_width >= LARGE_LAYOUT_MIN_WIDTH
        if large != self._large:
            self._large = large
            self.header.configure(height=120 if large else 76)
            if self._seal_image is not None:
                self._seal_image.configure(size=(64, 64) if large else (48, 48))
            gap = 26 if large else 12
            for section in self._sections:
                section.pack_configure(pady=(0, gap))

        for label, reserved in self._wrap_labels:
            label.configure(wraplength=max(160, content_width - reserved))

    # ---- services ---------------------------------------------------------

    def _set_status_message(self, text):
        """The one-line note under the services bar (an adopted backend, a
        backend that stopped) - hidden when there's nothing to say."""
        if text:
            self.status_message.configure(text=text)
            if not self.status_message.winfo_ismapped():
                self.status_message.pack(fill="x", padx=2, pady=(10, 0))
        else:
            self.status_message.pack_forget()

    def _start_backend(self):
        python = venv_python()
        identity = backend_identity()
        if identity == "ours":
            self._backend_external = True
            self._append_log("[launcher] a backend is already running on port 8000 - using that one")
            self._set_status_message("Using a backend that was already running.")
            return
        if identity == "old":
            self._block_old_backend()
            return
        if not (BACKEND_DIR / "manage.py").exists():
            self._append_log(f"[launcher] backend not found at {BACKEND_DIR}")
            self._set_status("backend", "failed")
            self._set_status_message(f"Backend not found at {BACKEND_DIR}.")
            return
        self._backend_preparing = True
        self._backend_update_failed = False
        self._set_status("backend", "starting")

        # First bring the database up to date with this version of the code -
        # a newer installer, or a `git pull`, can add tables or columns, and
        # the backend would fail on the first request that touches them. A
        # second or two when there's nothing to do.
        def migrate():
            result = subprocess.run(
                [python, "manage.py", "migrate", "--noinput"], cwd=BACKEND_DIR, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=600, creationflags=_NO_WINDOW,
            )
            return result.returncode, (result.stdout + result.stderr).strip()

        def start(result):
            self._backend_preparing = False
            code, output = result if result else (1, "the database update didn't run")
            applied = [line.strip() for line in output.splitlines() if line.strip().startswith("Applying ")]
            for line in applied:
                self._append_log(f"[launcher] database update: {line}")
            if code != 0:
                self._backend_update_failed = True
                self._append_log(f"[launcher] couldn't update the database: {output[-2000:]}")
                self._set_status("backend", "failed")
                self._set_status_message("Couldn't update the database — open the log below to see why.")
                return
            self._append_log(f"[launcher] starting backend with {python}")
            # -u so Django's output reaches the log panel as it happens rather
            # than sitting in a pipe buffer until the process exits.
            # --noreload: no background helper re-checking every code file for
            # changes each second - real CPU on a low-power laptop, and not
            # needed while the system is running for real (settings change
            # from the dashboard, not by editing files). Restart the backend
            # after editing its code.
            self.backend.start([python, "-u", "manage.py", "runserver", "--noreload"], BACKEND_DIR,
                               env_overrides=device_setup.backend_env_overrides(self.profile))

        self._run_in_background(migrate, start)

    def _block_old_backend(self):
        """An older SecureTap's backend holds port 8000. Don't use it - and
        don't open the dashboard or gate monitor on it - until it's closed;
        _poll_health then starts this version's backend by itself."""
        already = self._backend_blocked
        self._backend_blocked = True
        self._backend_ok = False
        self._set_status("backend", "failed")
        self._set_status_message("An older SecureTap is still running on this computer. Close it (or restart the "
                                 "computer) — this one starts by itself once it's gone.")
        if not already:
            self._append_log("[launcher] an older SecureTap backend answers on port 8000 - not using it")
            self.root.after(300, lambda: messagebox.showwarning(
                "Older SecureTap still running",
                "An older copy of SecureTap is still running on this computer, so this one can't start yet.\n\n"
                "Close the old SecureTap (its launcher, gate monitor and any black command window), or restart "
                "the computer. This window starts SecureTap by itself as soon as the old one is gone.",
                parent=self.root,
            ))

    def _open_dashboard(self):
        """The backend serves the dashboard at DASHBOARD_URL, so this only
        makes sure the dashboard's ready-made build is up to date - rebuilding
        it when its code has changed since, which needs Node.js (a computer
        that only runs SecureTap never does) - then opens the browser as soon
        as the backend answers."""
        if self.dashboard.is_running():
            return  # already rebuilding - the browser opens when it's done
        self._dashboard_failed = False
        if not _dashboard_build_is_current():
            npm = npm_command()
            if npm is not None and (DASHBOARD_DIR / "node_modules").exists():
                self._append_log("[launcher] the dashboard's code changed since its last build - rebuilding it")
                self._dashboard_started = True
                self.dashboard.start([npm, "run", "build"], DASHBOARD_DIR)
                self._set_status("dashboard", "starting")
                self._start_spinner("dashboard", "Preparing the dashboard (only after its code changed)…")
                return
            if not _dashboard_build_exists():
                self._append_log("[launcher] dashboard/dist is missing and Node.js isn't available to build it")
                messagebox.showerror(
                    "Dashboard not built",
                    "The dashboard's ready-made files (dashboard/dist) are missing.\n\n"
                    "On a computer with Node.js, run 'npm install' and then 'npm run build' "
                    "in the dashboard folder.",
                )
                return
            self._append_log(
                "[launcher] the dashboard's code is newer than its build, but Node.js isn't available to "
                "rebuild it - opening the existing build"
            )
        self._request_dashboard_open()

    def _request_dashboard_open(self):
        if self._backend_ok:
            self._launch_browser()
        else:
            self._open_when_backend_ready = True
            self._start_spinner("dashboard", "Waiting for the backend to finish starting…")

    def _handle_dashboard_output(self, line):
        """The dashboard rebuild's output, from its reader thread - log only;
        _apply_health notices when it's finished, on the Tk thread."""
        self._append_log(line)

    def _launch_browser(self):
        self._browser_opened = True
        self._open_when_backend_ready = False
        self._append_log(f"[launcher] opening {DASHBOARD_URL}")
        self._stop_spinner("dashboard")
        self._set_card_state("dashboard", "running", "Open at localhost:8000 — click to open it again")
        webbrowser.open(DASHBOARD_URL)

    def _run_in_background(self, work, on_done):
        """Runs work() off the Tk thread, then on_done(result) back on it.
        For the network and device checks: on Windows a connection to a port
        nothing is listening on takes about 2s to fail, and doing that on
        the Tk thread froze the whole window for that long. on_done gets
        None if work() raised (the error goes to the log)."""
        box = {}

        def runner():
            try:
                box["result"] = work()
            except Exception as exc:  # a failed check must never take the launcher down
                box["error"] = exc

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()

        def check():
            if thread.is_alive():
                self.root.after(50, check)
                return
            if "error" in box:
                self._append_log(f"[launcher] background check failed: {box['error']}")
            on_done(box.get("result"))

        self.root.after(50, check)

    def _open_entry_agent(self):
        if self.entry_agent.is_running():
            self._append_log("[launcher] entry-agent is already running")
            return
        if self._preflight_running:
            return  # already checking from an earlier click
        if not (ENTRY_AGENT_DIR / "main.py").exists():
            self._append_log(f"[launcher] entry-agent not found at {ENTRY_AGENT_DIR}")
            return

        self._save_current_settings()
        self._preflight_running = True
        self._start_spinner("entry-agent", "Checking the backend and card reader…")
        self._run_in_background(self._entry_agent_preflight_warnings, self._finish_opening_entry_agent)

    def _finish_opening_entry_agent(self, warnings):
        self._preflight_running = False
        if warnings:
            self._append_log("[launcher] pre-flight check: " + " | ".join(text for _name, text in warnings))
            if not self._preflight_dialog(warnings):
                self._stop_spinner("entry-agent")
                self._set_card_state("entry-agent", None)
                self._append_log("[launcher] opening the gate monitor was cancelled at the pre-flight warning")
                return

        self._append_log(
            f"[launcher] starting entry-agent (gate={self.settings['gate_location']!r}, "
            f"direction={self.settings['direction']!r})"
        )
        self._entry_agent_ready = False
        # GATE_LOCATION/DIRECTION/OFFICER_NAME are the exact names
        # entry-agent/config.py already reads from the environment (with its
        # own entry-agent/.env-file defaults) - passing them as subprocess
        # environment overrides means this needed zero entry-agent code
        # changes, and doesn't touch the .env file a technician maintains by
        # hand. OFFICER_NAME is only overridden when the guard actually typed
        # one - an empty override would blank out .env's own default instead
        # of leaving it alone.
        env_overrides = {
            "GATE_LOCATION": self.settings["gate_location"],
            "DIRECTION": self.settings["direction"],
        }
        if self.settings.get("officer_name"):
            env_overrides["OFFICER_NAME"] = self.settings["officer_name"]
        # The speed mode's video smoothness, upload size and scan pace.
        env_overrides.update(device_setup.entry_agent_env_overrides(self.profile))
        self.entry_agent.start([venv_python(), "-u", "main.py"], ENTRY_AGENT_DIR, env_overrides=env_overrides)
        self._set_status("entry-agent", "starting")
        # Names what's actually taking the time, so the wait reads as work
        # rather than as the button having missed the click.
        self._start_spinner("entry-agent", "Starting the camera and opening the gate monitor…")

    def _entry_agent_preflight_warnings(self):
        """Fast, best-effort checks before opening the gate monitor, run off
        the Tk thread (see _run_in_background) - returns the warnings to
        show, if any. A warning never *stops* it on its own - a false
        negative here (backend slow to answer, the NFC check itself failing)
        mustn't be able to block a live demo - it just gives the guard the
        choice. Camera presence is NOT checked here on purpose: the gate
        monitor already handles "no camera" gracefully on its own
        (placeholder + camera picker + hot-plug reconnect, see ui.py), and
        checking here would mean importing cv2 into the launcher's own
        process just for this."""
        warnings = []
        identity = backend_identity(timeout=1.5)
        if identity == "old":
            warnings.append(("plugs", "An older SecureTap is still running on this computer — the gate monitor "
                                      "would log into the old system. Close the old SecureTap first."))
        elif identity is None:
            warnings.append(("plugs", "The backend isn't responding yet — entry/exit logging won't work until it is."))
        reader_ids = _nfc_reader_usb_ids()
        if _detect_nfc_reader(reader_ids) is False:
            warnings.append((
                "identification-card",
                "No NFC card reader was detected (looked for USB IDs " + ", ".join(reader_ids) + "). "
                "If yours is plugged in, add its ID to NFC_READER_USB_IDS in entry-agent/.env.",
            ))
        return warnings

    def _preflight_dialog(self, warnings):
        """A modal "Before you open the gate monitor" dialog in the app's own
        look (a messagebox can't show per-warning icons or a primary
        button). Returns True for "Open gate monitor", False for Cancel or
        closing the dialog."""
        dialog = ctk.CTkToplevel(self.root)
        dialog.title("EVSU SecureTap")
        dialog.configure(fg_color=SURFACE)
        dialog.resizable(False, False)
        dialog.transient(self.root)
        # CTkToplevel resets its icon shortly after creation - set ours after.
        dialog.after(250, lambda: _apply_icon(dialog))
        result = {"open": False}

        body = ctk.CTkFrame(dialog, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=26, pady=(26, 20))
        heading = ctk.CTkFrame(body, fg_color="transparent")
        heading.pack(fill="x")
        _icon_label(heading, "warning", 26, CAUTION).pack(side="left", padx=(0, 10))
        ctk.CTkLabel(heading, text="Before you open the gate monitor", font=(SEMI_HEAVY, 20), text_color=INK,
                     anchor="w").pack(side="left")

        items = ctk.CTkFrame(body, fg_color="transparent")
        items.pack(fill="x", pady=(16, 0))
        _hairline(items)
        for icon_name, text in warnings:
            row = ctk.CTkFrame(items, fg_color="transparent")
            row.pack(fill="x", pady=10)
            _icon_label(row, icon_name, 18, CAUTION).pack(side="left", anchor="n", padx=(0, 10))
            ctk.CTkLabel(row, text=text, font=(FONT, 14), text_color=INK, anchor="w", justify="left",
                         wraplength=360).pack(side="left", fill="x", expand=True)
            _hairline(items)

        ctk.CTkLabel(body, text="The gate monitor will still open.", font=(FONT, 14, "bold"), text_color=INK,
                     anchor="w").pack(fill="x", pady=(16, 0))

        def finish(open_it):
            result["open"] = open_it
            dialog.grab_release()
            dialog.destroy()

        buttons = ctk.CTkFrame(body, fg_color="transparent")
        buttons.pack(fill="x", pady=(22, 0))
        ctk.CTkButton(
            buttons, text="Open gate monitor  →", height=44, corner_radius=8, fg_color=MAROON,
            hover_color=MAROON_DEEP, text_color="white", font=(FONT, 14, "bold"), command=lambda: finish(True),
        ).pack(side="right")
        ctk.CTkButton(
            buttons, text="Cancel", width=90, height=44, corner_radius=8, fg_color=SURFACE, hover_color=CANVAS,
            text_color=MAROON, font=(FONT, 14, "bold"), command=lambda: finish(False),
        ).pack(side="right", padx=(0, 10))

        dialog.protocol("WM_DELETE_WINDOW", lambda: finish(False))
        dialog.bind("<Escape>", lambda _event: finish(False))
        dialog.bind("<Return>", lambda _event: finish(True))

        # Centered over the launcher. Position set through plain Tk (CTk's
        # own geometry() would rescale the offsets for the display DPI).
        dialog.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dialog.winfo_width()) // 2
        y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - dialog.winfo_height()) // 3)
        tk.Toplevel.geometry(dialog, f"+{max(0, x)}+{max(0, y)}")
        dialog.grab_set()
        dialog.focus_force()
        self.root.wait_window(dialog)
        return result["open"]

    def _handle_entry_agent_output(self, line):
        """Reader-thread side: record only. The Tk thread reacts in _flush_log."""
        self._append_log(line)
        if ENTRY_AGENT_READY_MARKER in line:
            self._entry_agent_ready = True

    # ---- settings -----------------------------------------------------------

    def _toggle_settings_panel(self):
        if self._settings_panel_visible:
            self.settings_panel.pack_forget()
            self.settings_rule.pack_forget()
            self.settings_caret.configure(text=_icon("caret-right"))
        else:
            self.settings_rule.pack(fill="x", padx=1, before=self.speed_rule)
            self.settings_panel.pack(fill="x", before=self.speed_rule)
            self.settings_caret.configure(text=_icon("caret-down"))
        self._settings_panel_visible = not self._settings_panel_visible

    # ---- speed mode -----------------------------------------------------------

    def _toggle_speed_panel(self):
        if self._speed_panel_visible:
            self.speed_panel.pack_forget()
            self.speed_panel_rule.pack_forget()
            self.speed_caret.configure(text=_icon("caret-right"))
        else:
            self.speed_panel_rule.pack(fill="x", padx=1, before=self.backup_rule)
            self.speed_panel.pack(fill="x", before=self.backup_rule)
            self.speed_caret.configure(text=_icon("caret-down"))
        self._speed_panel_visible = not self._speed_panel_visible

    def _outline_button(self, parent, text, command):
        return ctk.CTkButton(
            parent, text=text, font=(FONT, 14, "bold"), height=40, corner_radius=8, fg_color=SURFACE,
            hover_color=CANVAS, text_color=INK, border_width=1, border_color=LINE, command=command,
        )

    def _mode_toggle(self, parent):
        """Fast | Standard | Light, styled like the Entry | Exit toggle."""
        frame = ctk.CTkFrame(parent, fg_color=SURFACE, corner_radius=3, border_width=1, border_color=LINE,
                             width=330, height=44)
        frame.grid_propagate(False)
        frame.grid_rowconfigure(0, weight=1)
        self._mode_halves = {}
        for index, (key, mode) in enumerate(device_setup.MODES.items()):
            if index:
                tk.Frame(frame, bg=LINE, width=1).grid(row=0, column=index * 2 - 1, sticky="ns", pady=1)
            frame.grid_columnconfigure(index * 2, weight=1, uniform="mode")
            cell = ctk.CTkFrame(frame, fg_color=SURFACE, corner_radius=0, cursor="hand2")
            # The last cell keeps 2px clear on the right, or it covers the
            # toggle's own border there.
            last = index == len(device_setup.MODES) - 1
            cell.grid(row=0, column=index * 2, sticky="nsew", padx=(1, 2) if last else 1, pady=1)
            text = ctk.CTkLabel(cell, text=mode.label, font=(FONT, 14, "bold"), text_color=INK_600, cursor="hand2")
            text.place(relx=0.5, rely=0.5, anchor="center")
            for widget in (cell, text):
                widget.bind("<Button-1>", lambda _event, k=key: self._pick_speed_mode(k))
            self._mode_halves[key] = (cell, text)
        return frame

    def _render_speed(self):
        current = device_setup.mode_of(self.profile)
        for key, (cell, text) in self._mode_halves.items():
            selected = key == current
            background = INK if selected else SURFACE
            cell.configure(fg_color=background)
            text.configure(text_color="white" if selected else INK_600, fg_color=background)
        self.speed_summary.configure(text=device_setup.MODES[current].label)
        detail = device_setup.MODES[current].summary
        measured = (self.profile or {}).get("measured")
        if measured:
            recommended = device_setup.MODES[self.profile.get("recommended") or current].label
            detail += (f"\n\nLast speed test: each face check took {measured['ms_median']:.0f} ms on "
                       f"{measured['processors']} processor cores - it recommended {recommended}.")
        else:
            detail += "\n\nNo speed test has run on this computer yet."
        self.speed_detail.configure(text=detail)
        if not self._speed_test_running:
            self.speed_test_button.configure(text=self._speed_test_label())

    def _speed_test_label(self):
        return "Run the speed test again" if (self.profile or {}).get("measured") else "Run the speed test"

    def _pick_speed_mode(self, key, chosen_by=None, **measurement):
        if key == device_setup.mode_of(self.profile) and not measurement:
            return
        before = device_setup.backend_env_overrides(self.profile)
        recommended = measurement.get("recommended") or (self.profile or {}).get("recommended")
        try:
            self.profile = device_setup.save_profile(
                key, chosen_by or ("speed test" if key == recommended else "you"), **measurement)
        except OSError as exc:
            self.speed_note.configure(text=f"Couldn't save the speed mode: {exc}")
            return
        self._render_speed()
        self._append_log(f"[launcher] speed mode: {device_setup.describe(self.profile)}")
        notes = [f"Speed mode is now {device_setup.MODES[key].label}."]
        if device_setup.backend_env_overrides(self.profile) != before:
            if self._backend_external:
                notes.append("Restart the backend you started yourself so it uses it.")
            elif self.backend.is_running():
                # The backend reads its detection size once, at start - restart
                # it now rather than leaving the new mode half-applied.
                self.backend.stop()
                self._backend_ok = False
                self._start_backend()
                notes.append("The backend restarted to use it.")
        if self.entry_agent.is_running():
            notes.append("Close and reopen the gate monitor to use it there.")
        self.speed_note.configure(text=" ".join(notes))

    def _run_speed_test(self):
        if self._speed_test_running:
            return
        self._speed_test_running = True
        self.speed_test_button.configure(state="disabled", text="Testing… (about half a minute)")
        busy = " The gate monitor is open, so the result may come out a little slow." if \
            self.entry_agent.is_running() else ""
        self.speed_note.configure(text="Measuring how fast this computer runs the face check…" + busy)

        def work():
            measured = device_setup.run_speed_test(venv_python())
            return measured, device_setup.recommend(measured)

        def done(result):
            self._speed_test_running = False
            self.speed_test_button.configure(state="normal", text=self._speed_test_label())
            if result is None:
                self.speed_note.configure(text="The speed test couldn't run - open the log below to see why.")
                return
            measured, (recommended, reason) = result
            self._append_log(f"[launcher] speed test: {measured}")
            self._pick_speed_mode(recommended, "speed test", measured=measured, recommended=recommended,
                                  reason=reason)
            self.speed_note.configure(text=reason + " " + self.speed_note.cget("text"))

        self._run_in_background(work, done)

    def _request_setup(self):
        """Closes this window and reopens the first-run setup (main() does the
        reopening) - e.g. to bring data over from another copy later."""
        if not messagebox.askokcancel(
            "Run setup again",
            "This closes the launcher and stops everything it started, then opens the setup window. "
            "Nothing is deleted unless you choose to replace this computer's data there.",
        ):
            return
        self.setup_requested = True
        self._stop_all()
        self.root.destroy()

    # ---- backup ---------------------------------------------------------------

    def _toggle_backup_panel(self):
        if self._backup_panel_visible:
            self.backup_panel.pack_forget()
            self.backup_panel_rule.pack_forget()
            self.backup_caret.configure(text=_icon("caret-right"))
        else:
            self.backup_panel_rule.pack(fill="x", padx=1, before=self.log_rule)
            self.backup_panel.pack(fill="x", before=self.log_rule)
            self.backup_caret.configure(text=_icon("caret-down"))
        self._backup_panel_visible = not self._backup_panel_visible

    def _render_backup(self):
        """The row's hint: when the last backup was made - in caution colour
        when there's none, or it's more than BACKUP_REMINDER_DAYS old."""
        try:
            last = datetime.fromisoformat(self.settings["last_backup_at"])
        except (KeyError, TypeError, ValueError):
            self.backup_summary.configure(text="Never backed up", text_color=CAUTION)
            return
        old = (datetime.now() - last).days > BACKUP_REMINDER_DAYS
        self.backup_summary.configure(text=f"Last: {last:%d %b %Y}", text_color=CAUTION if old else INK_600)

    def _backup_password_dialog(self):
        """Asks for the backup's password twice, in the app's own look.
        Returns the password, or None if cancelled."""
        dialog = ctk.CTkToplevel(self.root)
        dialog.title("EVSU SecureTap")
        dialog.configure(fg_color=SURFACE)
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.after(250, lambda: _apply_icon(dialog))
        result = {"password": None}

        body = ctk.CTkFrame(dialog, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=26, pady=(26, 20))
        heading = ctk.CTkFrame(body, fg_color="transparent")
        heading.pack(fill="x")
        _icon_label(heading, "lock-simple", 24, INK).pack(side="left", padx=(0, 10))
        ctk.CTkLabel(heading, text="Lock the backup with a password", font=(SEMI_HEAVY, 20), text_color=INK,
                     anchor="w").pack(side="left")
        ctk.CTkLabel(
            body, text=f"At least {BACKUP_MIN_PASSWORD} characters. The backup can't be opened without it - not "
                       "even by the SecureTap team - so write it down and keep it apart from the USB drive.",
            font=(FONT, 14), text_color=INK, anchor="w", justify="left", wraplength=380,
        ).pack(fill="x", pady=(12, 0))
        fields = []
        for label in ("Password", "Type it again"):
            ctk.CTkLabel(body, text=label, font=(FONT, 14, "bold"), text_color=INK, anchor="w").pack(
                fill="x", pady=(14, 0))
            entry = self._entry(body)
            entry.configure(show="•")
            entry.pack(fill="x", pady=(6, 0))
            fields.append(entry)
        error = ctk.CTkLabel(body, text="", font=(FONT, 13, "bold"), text_color=DANGER, anchor="w", justify="left",
                             wraplength=380)
        error.pack(fill="x", pady=(8, 0))

        def finish(save):
            if save:
                password, again = fields[0].get(), fields[1].get()
                if len(password) < BACKUP_MIN_PASSWORD:
                    error.configure(text=f"Use at least {BACKUP_MIN_PASSWORD} characters.")
                    return
                if password != again:
                    error.configure(text="The two passwords aren't the same.")
                    return
                result["password"] = password
            dialog.grab_release()
            dialog.destroy()

        buttons = ctk.CTkFrame(body, fg_color="transparent")
        buttons.pack(fill="x", pady=(14, 0))
        ctk.CTkButton(
            buttons, text="Choose where to save  →", height=44, corner_radius=8, fg_color=MAROON,
            hover_color=MAROON_DEEP, text_color="white", font=(FONT, 14, "bold"), command=lambda: finish(True),
        ).pack(side="right")
        ctk.CTkButton(
            buttons, text="Cancel", width=90, height=44, corner_radius=8, fg_color=SURFACE, hover_color=CANVAS,
            text_color=MAROON, font=(FONT, 14, "bold"), command=lambda: finish(False),
        ).pack(side="right", padx=(0, 10))

        dialog.protocol("WM_DELETE_WINDOW", lambda: finish(False))
        dialog.bind("<Escape>", lambda _event: finish(False))
        dialog.bind("<Return>", lambda _event: finish(True))
        # Centered over the launcher, by the size it asks for - its actual
        # size isn't known until Windows has drawn it.
        dialog.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dialog.winfo_reqwidth()) // 2
        y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - dialog.winfo_reqheight()) // 3)
        tk.Toplevel.geometry(dialog, f"+{max(0, x)}+{max(0, y)}")
        dialog.grab_set()
        dialog.focus_force()
        fields[0].focus_set()
        self.root.wait_window(dialog)
        return result["password"]

    def _back_up(self):
        """Password, then a folder, then backend/manage.py backup_data in the
        background (the password goes in through its input, never on its
        command line). Safe while the backend and the gate monitor run."""
        if self._backup_running:
            return
        password = self._backup_password_dialog()
        if not password:
            return
        folder = filedialog.askdirectory(parent=self.root, title="Where should the backup go? (a USB drive, for example)")
        if not folder:
            return
        data_drive = os.path.splitdrive(str(device_setup.DATA_DIR or ROOT))[0].upper()
        if os.path.splitdrive(folder)[0].upper() == data_drive and not messagebox.askokcancel(
            "Back up data",
            "That folder is on this computer's own drive. If the computer breaks or is lost, the backup is lost "
            "with it - a USB drive is safer.\n\nSave it there anyway?", parent=self.root,
        ):
            return
        self._backup_running = True
        self.backup_button.configure(state="disabled", text="Backing up…")
        self.backup_note.configure(text="Saving the backup - a minute or so when there are many photos.",
                                   text_color=INK_600)

        def work():
            result = subprocess.run(
                [venv_python(), "manage.py", "backup_data", folder], cwd=BACKEND_DIR, input=password + "\n",
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600,
                creationflags=_NO_WINDOW,
            )
            return result.returncode, (result.stdout + result.stderr).strip()

        def done(outcome):
            self._backup_running = False
            self.backup_button.configure(state="normal", text="Back up now…")
            code, output = outcome if outcome else (1, "the backup didn't run")
            lines = [line for line in output.splitlines() if line.strip()]
            for line in lines:
                if not line.startswith(BACKUP_RESULT_MARKER):
                    self._append_log(f"[backup] {line}")
            result = next((line for line in reversed(lines) if line.startswith(BACKUP_RESULT_MARKER)), None)
            if code != 0 or result is None:
                reason = next((line.split("Error:", 1)[1].strip() for line in reversed(lines) if "Error:" in line),
                              lines[-1] if lines else "unknown error")
                self.backup_note.configure(text=f"Couldn't make the backup: {reason}", text_color=DANGER)
                return
            made = json.loads(result.split(" ", 1)[1])
            self.settings.update(last_backup_at=datetime.now().isoformat(timespec="minutes"),
                                 last_backup_file=made["file"])
            _save_settings(self.settings)
            self._render_backup()
            self.backup_note.configure(
                text=f"Saved: {made['file']} ({made['size_mb']} MB - {made['people']} people, {made['entry_logs']} "
                     "entry records). Keep the password safe: without it the backup can't be opened.",
                text_color=VERIFIED,
            )

        self._run_in_background(work, done)

    def _current_entry_agent_settings(self):
        """Reads the settings widgets directly rather than trusting whatever
        was last saved - covers the case where a field was edited but never
        blurred (no <FocusOut> fired yet) before "Entry Agent" was clicked."""
        return {
            "gate_location": self.gate_entry.get().strip() or DEFAULT_GATE_LOCATION,
            "direction": self._direction,
            "officer_name": self.guard_name_entry.get().strip(),
            "auto_launch_entry_agent": bool(self.auto_launch_var.get()),
        }

    def _save_current_settings(self):
        # Merged, so what else is remembered here (the last backup) stays.
        self.settings = {**self.settings, **self._current_entry_agent_settings()}
        _save_settings(self.settings)
        self._update_settings_summary()

    def _update_settings_summary(self):
        gate = self.gate_entry.get().strip() or DEFAULT_GATE_LOCATION
        self.settings_summary.configure(text=f"{gate} · {self._direction.capitalize()}")

    # ---- loading state ------------------------------------------------------

    def _start_spinner(self, key, message):
        """Puts a choice card into its "starting" state and watches for the
        startup timeout. Driven by Tk's after() rather than a thread - it's
        the main loop's own timer, so there's no cross-thread widget access
        to get wrong, and it stops dead if the window closes."""
        self._stop_spinner(key)
        self._spinners[key] = {"job": None, "elapsed": 0}
        self._set_card_state(key, "starting", message)
        self._tick_spinner(key)

    def _tick_spinner(self, key):
        state = self._spinners.get(key)
        if state is None:
            return
        state["elapsed"] += SPINNER_INTERVAL_MS
        if state["elapsed"] >= STARTUP_TIMEOUT_MS:
            self._stop_spinner(key, "Still not up after 45s — open the log below to see what happened", "slow")
            return
        state["job"] = self.root.after(SPINNER_INTERVAL_MS, lambda: self._tick_spinner(key))

    def _stop_spinner(self, key, final_text=None, kind="running"):
        state = self._spinners.pop(key, None)
        if state is None:
            return
        if state["job"] is not None:
            self.root.after_cancel(state["job"])
        if final_text is not None:
            self._set_card_state(key, kind, final_text)

    def _spinning(self, key):
        return key in self._spinners

    # ---- status -----------------------------------------------------------

    def _set_status(self, key, state):
        word, color, icon_name = SERVICE_STATES[state]
        icon, label = self.status_widgets[key]
        if label.cget("text") == word and label.cget("text_color") == color:
            return  # polled every 2s - skip redundant reconfigures
        icon.configure(text=_icon(icon_name), text_color=color)
        label.configure(text=word, text_color=color)
        # A different word ("Starting…" vs "Not running") needs a different
        # width - re-check whether the cells still fit on one line.
        self.root.after_idle(self._fit_services)

    def _poll_health(self):
        """Backend liveness plus a liveness check on each child process, so a
        service that died (a bad .env, a port already taken) turns red here
        instead of just never becoming ready. The two network probes run off
        the Tk thread (see _run_in_background) - while the backend is down or
        still starting, each one takes up to 1.5s to give up, which used to
        freeze the window on every poll."""
        def probe():
            return backend_identity(timeout=1.5)

        def apply(identity):
            if self._backend_blocked:
                if identity is None:
                    # The old SecureTap has been closed - start ours now.
                    self._backend_blocked = False
                    self._append_log("[launcher] the older SecureTap is gone - starting the backend")
                    self._set_status_message(None)
                    self._start_backend()
                self.root.after(HEALTH_POLL_MS, self._poll_health)
                return
            if identity == "old" and not self.backend.is_running():
                self._block_old_backend()
                self.root.after(HEALTH_POLL_MS, self._poll_health)
                return
            self._apply_health(identity == "ours")
            self.root.after(HEALTH_POLL_MS, self._poll_health)

        self._run_in_background(probe, apply)

    def _maybe_run_cleanup(self):
        """Runs the data clean-up once the backend is up, then once a day
        while the launcher stays open - this project has no job scheduler,
        and the launcher is how the system is started. The command itself
        does nothing unless "Automatic deletion" is on in the dashboard's
        Settings page, so this is harmless while that's off."""
        now = time.monotonic()
        if self._cleanup_running or (
            self._last_cleanup_at is not None and now - self._last_cleanup_at < CLEANUP_INTERVAL_SECONDS
        ):
            return
        self._cleanup_running = True
        self._last_cleanup_at = now

        def work():
            result = subprocess.run(
                [venv_python(), "manage.py", "purge_old_data"], cwd=BACKEND_DIR, capture_output=True,
                text=True, timeout=600, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return (result.stdout + result.stderr).strip()

        def done(output):
            self._cleanup_running = False
            self._append_log(f"[launcher] daily data clean-up: {output or 'could not run - see above'}")

        self._run_in_background(work, done)

    def _apply_health(self, backend_up):
        if backend_up:
            self._maybe_run_cleanup()
            if not self._backend_ok:
                self._append_log("[launcher] backend is up")
            self._backend_ok = True
            self._set_status("backend", "ready")
            self._set_status_message("Using a backend that was already running." if self._backend_external else None)
        elif self.backend.is_running() or self._backend_preparing:
            self._backend_ok = False
            self._set_status("backend", "starting")
        elif self._backend_update_failed:
            self._backend_ok = False
        else:
            self._backend_ok = False
            self._set_status("backend", "stopped")
            self._set_status_message("The backend stopped — open the log below to see why.")

        # The dashboard is served by the backend, so it's ready whenever the
        # backend is and its build exists; "Starting…" only while rebuilding.
        if self.dashboard.is_running():
            self._set_status("dashboard", "starting")
        elif self._dashboard_started:
            # A rebuild just finished.
            self._dashboard_started = False
            if _dashboard_build_is_current():
                self._append_log("[launcher] dashboard rebuilt")
                self._request_dashboard_open()
            else:
                self._dashboard_failed = True
                self._stop_spinner("dashboard", "Couldn't prepare the dashboard — open the log below to see why",
                                   "failed")
        if self._dashboard_failed:
            self._set_status("dashboard", "failed")
        elif not self.dashboard.is_running():
            self._set_status("dashboard", "ready" if backend_up and _dashboard_build_exists() else "off")
        if backend_up and self._open_when_backend_ready:
            self._launch_browser()

        if self.entry_agent.is_running():
            self._entry_agent_started = True
            self._set_status("entry-agent", "running")
        elif self._spinning("entry-agent"):
            # It exited before ever signalling ready - almost always a camera
            # that wouldn't open or a bad .env, and the traceback is in the log.
            self._stop_spinner("entry-agent", "Failed to start — open the log below to see why", "failed")
            self._set_status("entry-agent", "failed")
            # See the dashboard branch above.
            self._entry_agent_started = False
        else:
            self._set_status("entry-agent", "off")
            if self._entry_agent_started:
                # Ran and was closed normally - clear the card's state strip
                # rather than leaving "Gate monitor is open" next to a window
                # that isn't.
                self._entry_agent_started = False
                self._set_card_state("entry-agent", None)

    # ---- log ------------------------------------------------------------

    def _append_log(self, line):
        """Called from output-reader threads, so it only touches the deque -
        the widget itself is updated by _flush_log on the Tk main thread."""
        with self._log_lock:
            self._log.append(line)
            self._log_dirty = True

    def _flush_log(self):
        # Also where signals spotted by the output-reader threads get acted on,
        # since this already runs on the Tk main thread.
        if self._entry_agent_ready and self._spinning("entry-agent"):
            self._stop_spinner("entry-agent", "Gate monitor is open — check your taskbar if you don't see it")
        with self._log_lock:
            count = len(self._log)
        self.log_hint.configure(text=f"{count} line{'s' if count != 1 else ''}")
        if self._log_visible and self._log_dirty:
            with self._log_lock:
                text = "\n".join(self._log)
                self._log_dirty = False
            self.log_box.configure(state="normal")
            self.log_box.delete("1.0", "end")
            self.log_box.insert("1.0", text)
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
        self.root.after(LOG_REFRESH_MS, self._flush_log)

    def _toggle_log(self):
        if self._log_visible:
            self.log_box.pack_forget()
            self.log_caret.configure(text=_icon("caret-right"))
            self._log_visible = False
        else:
            self.log_box.pack(fill="x", padx=16, pady=(0, 16))
            self.log_caret.configure(text=_icon("caret-down"))
            self._log_visible = True
            self._log_dirty = True  # force one immediate repaint

    # ---- shutdown -------------------------------------------------------

    def _handle_quit(self):
        running = [
            name
            for name, process in (
                ("the backend", self.backend),
                ("the dashboard", self.dashboard),
                ("the entry agent", self.entry_agent),
            )
            if process.is_running()
        ]
        if running:
            confirmed = messagebox.askokcancel(
                "Quit EVSU SecureTap",
                "This will stop " + ", ".join(running) + ".\n\nQuit anyway?",
            )
            if not confirmed:
                return
        self._stop_all()
        self.root.destroy()

    def _stop_all(self):
        """Stop every child we started. Safe to call twice - stop() is a no-op
        once a process is gone, and a no-op for a service we merely adopted."""
        for process in (self.entry_agent, self.dashboard, self.backend):
            process.stop()

    def run(self):
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            # Ctrl+C in the console that started us. Without catching it the
            # exception unwinds straight past _handle_quit, so nothing gets
            # stopped and every child we spawned is orphaned - a Django server
            # left holding port 8000 (and maybe a dashboard build) with no window
            # left to stop them from, and Task Manager as the only way out.
            print("\nInterrupted - stopping everything the launcher started...", file=sys.stderr)
        finally:
            # Runs on the normal quit path too; stop() being idempotent is what
            # makes that harmless.
            self._stop_all()


def main():
    # Distinct from the entry-agent's own id (see entry-agent/main.py) - each
    # process needs its own so Windows' taskbar treats them as separate apps
    # with separate icons, rather than grouping both under plain python.exe's.
    set_app_user_model_id("EVSU.SecureTap.Launcher")
    if not (ROOT / ".venv").exists() and not (ROOT / "python").exists() and sys.stderr is not None:
        print(
            "WARNING: no .venv at the repo root - falling back to the interpreter "
            "running this script. See README.md if imports fail.",
            file=sys.stderr,
        )
    run_setup = device_setup.needs_setup()
    while True:
        if run_setup:
            from setup_wizard import SetupWizard

            wizard = SetupWizard(
                venv_python(),
                backend_running=lambda: service_responds(HEALTH_URL, timeout=1.5),
                reader_check=lambda: _detect_nfc_reader(_nfc_reader_usb_ids()),
            )
            if not wizard.run():
                return  # setup was closed before it finished - it opens again next time
        launcher = LauncherWindow()
        launcher.run()
        if not launcher.setup_requested:
            return
        run_setup = True


if __name__ == "__main__":
    main()
