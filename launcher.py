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
because MySQL isn't running would just look like a button that does nothing.

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
import tkinter as tk
import webbrowser
from collections import deque
from datetime import datetime
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk
import requests
from dotenv import dotenv_values
from PIL import Image, ImageDraw, ImageTk

ROOT = Path(__file__).resolve().parent

# The design system lives with the entry-agent's UI. Imported rather than
# duplicated so the launcher and the gate monitor can't drift into looking
# like two different products - they're one system with one look.
sys.path.insert(0, str(ROOT / "entry-agent"))
from ui import (  # noqa: E402
    BG,
    BORDER,
    CARD_BG,
    DANGER,
    FONT,
    ICON_PATH,
    MAROON,
    MAROON_DARK,
    MAROON_LIGHT,
    SUCCESS,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    WARNING,
    HeaderBar,
    _apply_icon,
    _HoverAnimator,
    set_app_user_model_id,
)

BACKEND_DIR = ROOT / "backend"
DASHBOARD_DIR = ROOT / "dashboard"
ENTRY_AGENT_DIR = ROOT / "entry-agent"

HEALTH_URL = "http://127.0.0.1:8000/api/health"
FALLBACK_DASHBOARD_URL = "http://localhost:5173"
# Vite prints the URL it actually bound to, which isn't always 5173 - it walks
# up a port at a time when one's taken. Parsing the real one beats opening a
# browser at a guess.
VITE_URL_PATTERN = re.compile(r"https?://(?:localhost|127\.0\.0\.1):\d+")

HEALTH_POLL_MS = 2000
LOG_MAX_LINES = 500
LOG_REFRESH_MS = 700

# Everything below the header lives in a content column capped at this width
# and centered - past this, the window just grows background, not widgets.
# Chosen to comfortably fit two side-by-side cards without either reading as
# oversized on a normal desktop monitor.
CONTENT_MAX_WIDTH = 960
# Content width (not raw window width) above which the two choice cards sit
# side by side instead of stacked - picked so each card still has enough
# room for its title+subtitle without wrapping awkwardly right at the edge.
CARDS_SIDE_BY_SIDE_BREAKPOINT = 700
DEFAULT_WINDOW_WIDTH = 560
# Taller than before (was 680) - the entry-agent settings panel and the
# last-session/unsynced-queue notices both add content above the log panel,
# and this keeps the restored window from opening with the log toggle
# pushed hard against the bottom edge.
DEFAULT_WINDOW_HEIGHT = 760

# The entry-agent takes a real few seconds to appear: importing cv2, opening
# the webcam through DirectShow, then building the Tk window. Rather than
# guessing at a duration, main.py prints this marker on the line just before it
# hands off to its main loop, and the launcher stops the spinner when it sees
# it - so the animation ends when the window is actually up, not when a timer
# says it should be. Keep in sync with entry-agent/main.py.
ENTRY_AGENT_READY_MARKER = "SECURETAP_ENTRY_AGENT_READY"

# A pulse rather than a rotating glyph: ● and · are already used elsewhere in
# this UI and are known to render in Segoe UI, where braille/arc spinner
# characters are a gamble.
SPINNER_FRAMES = ("●  ·  ·", "·  ●  ·", "·  ·  ●", "·  ●  ·")
SPINNER_INTERVAL_MS = 200
# If a service never signals ready, stop spinning and say so - a spinner that
# never stops is worse than an error message.
STARTUP_TIMEOUT_MS = 45000

# Resting descriptions for the two choice cards. Kept as constants because the
# subtitles double as live status text while something starts, and have to be
# restorable once it stops.
DASHBOARD_IDLE_SUBTITLE = "Web app - register users, live monitoring, logs, reports"
ENTRY_AGENT_IDLE_SUBTITLE = "Gate monitor - camera face recognition + NFC card reader"

# Keeps a child's own console window from flashing up alongside ours. The
# entry-agent's Tk windows are unaffected - this suppresses the console, not
# the GUI.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

# The header behind the top of the window is solid maroon; without a
# transition the flat light-gray body underneath it just abuts that block
# with no depth. This is how far down (in logical px) the tint fades back to
# the plain page background - past this the window is flat BG, same as
# before.
BACKGROUND_GRADIENT_HEIGHT = 220
_BACKGROUND_BASE_RGB = (0xF5, 0xF5, 0xF7)  # BG, as RGB
_BACKGROUND_TINT_RGB = (0xF8, 0xEC, 0xEC)  # a barely-there warm maroon tint


def _make_dashboard_icon(size=26, color="white"):
    """A small 2x2 tile glyph for the Dashboard card, drawn with PIL rather
    than an emoji character - Segoe UI Emoji renders full-color glyphs that
    would clash with this app's flat maroon/white palette everywhere else."""
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    gap = max(2, size // 9)
    tile = (size - 3 * gap) // 2
    for row in range(2):
        for col in range(2):
            x0 = gap + col * (tile + gap)
            y0 = gap + row * (tile + gap)
            draw.rounded_rectangle(
                [x0, y0, x0 + tile, y0 + tile], radius=max(2, tile // 4), fill=color
            )
    return image


def _make_camera_icon(size=26, color="white", accent=MAROON):
    """A simple camera-body-and-lens glyph for the Entry Agent card, drawn
    the same way as the dashboard icon above for a consistent look."""
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    body_top = size * 0.3
    body_bottom = size * 0.84
    draw.rounded_rectangle([size * 0.06, body_top, size * 0.94, body_bottom], radius=size * 0.12, fill=color)
    draw.rounded_rectangle(
        [size * 0.36, size * 0.12, size * 0.68, body_top + 2], radius=size * 0.05, fill=color
    )
    lens_r = size * 0.19
    cx, cy = size * 0.5, (body_top + body_bottom) / 2
    draw.ellipse([cx - lens_r, cy - lens_r, cx + lens_r, cy + lens_r], fill=accent)
    inner_r = lens_r * 0.5
    draw.ellipse([cx - inner_r, cy - inner_r, cx + inner_r, cy + inner_r], fill=color)
    return image


def venv_python():
    """The repo's shared virtualenv interpreter. Everything (Django, the
    entry-agent, InsightFace) is installed into the one .venv at the repo root,
    so both child processes use it. Falls back to whatever is running this
    launcher, which is the right answer when someone already activated a venv
    by hand."""
    candidate = ROOT / ".venv" / "Scripts" / ("python.exe" if os.name == "nt" else "python")
    return str(candidate) if candidate.exists() else sys.executable


def npm_command():
    """npm on Windows is npm.cmd - a batch file, not an .exe - so plain
    which("npm") can miss it. Returns None when Node isn't installed at all,
    which is worth saying out loud rather than failing with a bare
    FileNotFoundError."""
    return shutil.which("npm.cmd") or shutil.which("npm")


def service_responds(url, timeout=1.0):
    """Whether something is already serving this URL.

    Checked before starting anything, because plenty of people already have
    `runserver` or `npm run dev` open in a terminal. Spawning a second one
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
SETTINGS_PATH = ROOT / "launcher_settings.json"
DEFAULT_GATE_LOCATION = "Main Gate"
DEFAULT_DIRECTION = "entry"

# Where main.py's on_close() writes a small summary of the session just
# ended (entries/exits/unknown/spoof/occlusion counts, gate, direction, a
# timestamp) - see entry-agent/main.py's _write_last_session_summary. Read
# back here so the launcher can show "last session" before the entry-agent
# is even opened again.
LAST_SESSION_PATH = ENTRY_AGENT_DIR / "last_session.json"

# The NFC reader's own sqlite-backed retry queue (entry-agent/offline_queue.py)
# - read directly with a plain sqlite3 connection rather than importing
# OfflineQueue itself, since that class also wants a live ApiClient just to
# construct, which isn't needed for a read-only pending-count peek.
OFFLINE_QUEUE_DB_PATH = ENTRY_AGENT_DIR / "offline_queue.db"

# ACS ACR122U NFC reader's USB vendor:product ID - the specific reader this
# deployment uses. It identifies to Windows as a generic HID keyboard (that's
# how it "types" a scanned card's ID), so there's no NFC-specific API to ask
# "is a reader plugged in" - matching this VID:PID via Windows' own device
# list is a heuristic for THIS reader model specifically, not a general
# "is any NFC reader present" check.
ACR122U_VID_PID_PATTERN = "VID_072F.*PID_2200"


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
    env_path = ENTRY_AGENT_DIR / ".env"
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
    if not summary:
        return None
    try:
        ended = datetime.fromisoformat(summary["ended_at"]).astimezone().strftime("%b %d, %I:%M %p")
    except (KeyError, ValueError):
        ended = "an unknown time"
    return (
        f"Last session - {summary.get('gate_location', '?')} ({summary.get('direction', '?')}): "
        f"{summary.get('entries', 0)} entries, {summary.get('exits', 0)} exits, "
        f"{summary.get('unknown', 0)} unknown - ended {ended}"
    )


def _detect_acr122u_reader(timeout=2.0):
    """Best-effort ACR122U presence check via Windows' own PnP device list.
    Returns True/False, or None when the check itself couldn't run (not
    Windows, powershell missing/timed out) - None is deliberately NOT treated
    as "absent" by callers, since a failed check saying "no reader found"
    would be actively misleading during a demo."""
    if os.name != "nt":
        return None
    try:
        result = subprocess.run(
            [
                "powershell", "-NoProfile", "-NonInteractive", "-Command",
                "(Get-PnpDevice -PresentOnly | Where-Object "
                f"{{ $_.InstanceId -match '{ACR122U_VID_PID_PATTERN}' }}).Count",
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


class LauncherWindow:
    def __init__(self):
        self._log = deque(maxlen=LOG_MAX_LINES)
        self._log_dirty = False
        self._log_lock = threading.Lock()
        self._hover_animators = []
        self._dashboard_url = None
        self._browser_opened = False
        self._backend_ok = False
        # "External" = already running when we got here, so it's not ours to
        # start and not ours to kill on quit.
        self._backend_external = False
        self._dashboard_external = False
        self._spinners = {}
        # Set by the entry-agent's output-reader thread, acted on by the Tk
        # main thread in _flush_log - same rule as the Vite URL above.
        self._entry_agent_ready = False
        # "Did we start it and see it run", so the card's subtitle can be reset
        # once it stops instead of describing a window that's already closed.
        self._entry_agent_started = False
        self._dashboard_started = False

        self.settings = _load_settings()
        self.app_version = _read_app_version()

        self.backend = ManagedProcess("backend", self._append_log)
        self.dashboard = ManagedProcess("dashboard", self._handle_dashboard_output)
        self.entry_agent = ManagedProcess("entry-agent", self._handle_entry_agent_output)

        self.root = ctk.CTk()
        self.root.title("EVSU SecureTap")
        self.root.configure(fg_color=BG)
        self.root.geometry(f"{DEFAULT_WINDOW_WIDTH}x{DEFAULT_WINDOW_HEIGHT}")
        self.root.minsize(520, 680)
        self.root.protocol("WM_DELETE_WINDOW", self._handle_quit)
        _apply_icon(self.root)

        self._build_background()

        HeaderBar(
            self.root, "EVSU SecureTap", "Choose what to open",
            center=True, logo_path=ICON_PATH,
        ).pack(fill="x")

        # Everything below the header lives in this column instead of packing
        # straight onto self.root - a maximized 1920px window used to just
        # stretch every widget with it (absurdly wide card bars, a big dead
        # gap down the middle). pack_propagate(False) is what lets a plain
        # pack() width configuration actually stick instead of shrinking back
        # to fit whatever's inside it; _apply_responsive_layout (bound to
        # <Configure> below) is what keeps that width capped and re-picks the
        # card layout as the window is resized.
        self._cards_side_by_side = None  # None forces the first relayout call to actually apply
        self.content = ctk.CTkFrame(self.root, fg_color="transparent")
        self.content.pack(fill="y", expand=True)
        self.content.pack_propagate(False)

        self._build_status_row()
        ctk.CTkFrame(self.content, fg_color=BORDER, height=1).pack(fill="x")
        self._build_buttons()
        self._build_notices()
        self._build_entry_agent_settings()
        self._build_log_panel()
        self._build_footer()

        self.root.bind("<Configure>", self._on_root_configure)
        # Paints the correct width/card layout immediately, before the first
        # real <Configure> event fires - otherwise the content frame sits at
        # its just-created (~1px) width for a visible instant. Uses the same
        # width just requested via geometry() above, not winfo_reqwidth()
        # (unreliable before the window's first draw - often reports 1,
        # which "or 560"-style fallbacks wouldn't catch since 1 is truthy).
        self._apply_responsive_layout(DEFAULT_WINDOW_WIDTH)
        self._draw_background(DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT)

        self._start_backend()
        self.root.after(500, self._poll_health)
        self.root.after(LOG_REFRESH_MS, self._flush_log)

        if self.settings.get("auto_launch_entry_agent"):
            # Deferred rather than called immediately - lets the window
            # actually paint first, so "auto-launch" doesn't look like the
            # launcher hanging on a blank frame before the gate monitor's own
            # (separately spinner-covered) startup begins.
            self.root.after(1200, self._open_entry_agent)

    # ---- responsive layout ------------------------------------------------

    def _on_root_configure(self, event):
        # <Configure> also fires for every child widget's own size/position
        # changes, not just the root window's - without this guard, a status
        # chip repainting would trigger the same expensive relayout logic.
        if event.widget is not self.root:
            return
        self._apply_responsive_layout(event.width)
        self._draw_background(event.width, event.height)

    def _apply_responsive_layout(self, window_width):
        target_width = max(360, min(window_width, CONTENT_MAX_WIDTH))
        self.content.configure(width=target_width)

        should_be_side_by_side = target_width >= CARDS_SIDE_BY_SIDE_BREAKPOINT
        if should_be_side_by_side != self._cards_side_by_side:
            self._cards_side_by_side = should_be_side_by_side
            self._relayout_buttons(should_be_side_by_side)

    def _build_background(self):
        """A plain tk.Canvas placed under every other widget, painted with a
        soft gradient instead of a flat fill - see BACKGROUND_GRADIENT_HEIGHT
        above for why. place()d rather than pack()ed so it doesn't take part
        in the packer's layout at all; lower() puts it behind the widgets
        that ARE pack()ed onto root, which is what makes it a backdrop rather
        than something covering them."""
        self.background_canvas = tk.Canvas(self.root, highlightthickness=0, bd=0, bg=BG)
        self.background_canvas.place(x=0, y=0, relwidth=1, relheight=1)
        # Canvas.lower() is shadowed by the canvas-ITEM stacking API (it wants
        # a tag argument), so the general widget-stacking command has to be
        # called directly rather than through that method. Being created
        # first already puts this behind every widget built after it, so
        # this is belt-and-suspenders rather than load-bearing.
        self.background_canvas.tk.call("lower", self.background_canvas._w)
        self._background_image = None  # keeps the PhotoImage alive; Tk drops unreferenced ones
        self._background_size = None

    def _draw_background(self, width, height):
        if width <= 1 or height <= 1:
            return
        if self._background_size == (width, height):
            return  # skip repainting on every keystroke-sized <Configure> no-op
        self._background_size = (width, height)

        fade_height = min(BACKGROUND_GRADIENT_HEIGHT, height)
        column = Image.new("RGB", (1, height), _BACKGROUND_BASE_RGB)
        for y in range(fade_height):
            t = y / max(fade_height - 1, 1)
            rgb = tuple(
                round(_BACKGROUND_TINT_RGB[i] + (_BACKGROUND_BASE_RGB[i] - _BACKGROUND_TINT_RGB[i]) * t)
                for i in range(3)
            )
            column.putpixel((0, y), rgb)
        gradient = column.resize((width, height))

        self._background_image = ImageTk.PhotoImage(gradient)
        self.background_canvas.delete("all")
        self.background_canvas.create_image(0, 0, anchor="nw", image=self._background_image)

    # ---- layout ---------------------------------------------------------

    # One glyph per state so a status reads at a glance even before the color
    # registers - useful for a guard/panel member who might not clock a
    # muted-vs-bright color difference across a room, and it's how the gate
    # monitor's own status bar already communicates "camera ok" vs not.
    STATUS_GLYPHS = {"idle": "○", "starting": "◐", "ok": "●", "failed": "✕"}

    def _build_status_row(self):
        wrap = ctk.CTkFrame(self.content, fg_color="transparent")
        wrap.pack(fill="x")
        row = ctk.CTkFrame(wrap, fg_color="transparent")
        row.pack(pady=16)

        self.status_labels = {}
        for key, label in (("backend", "Backend"), ("dashboard", "Dashboard"), ("entry-agent", "Entry agent")):
            # Same pill construction as the gate monitor's status-bar chips
            # (CARD_BG fill, rounded, padded text) - one visual language
            # across the whole app rather than plain unstyled text here.
            item = ctk.CTkLabel(
                row, text=self._pad(f"{self.STATUS_GLYPHS['idle']}  {label}"), font=(FONT, 11),
                text_color=TEXT_MUTED, fg_color=CARD_BG, corner_radius=9, height=26,
            )
            item.pack(side="left", padx=6)
            self.status_labels[key] = (item, label)

    @staticmethod
    def _pad(text):
        """Pill-style CTkLabels get their horizontal breathing room from the
        text itself, matching the identical helper in entry-agent/ui.py."""
        return f"  {text}  "

    def _build_buttons(self):
        # grid, not pack, for the two cards specifically - _relayout_buttons
        # switches them between stacked (one column) and side-by-side (two
        # equal columns) by re-gridding these same two widgets, not rebuilding
        # them, so their hover-animator bindings/spinner state stay intact
        # across a resize.
        self.buttons_frame = ctk.CTkFrame(self.content, fg_color="transparent")
        self.buttons_frame.pack(fill="x", padx=28, pady=(24, 8))

        # Keep references alive - CTkImage/ImageTk objects are garbage
        # collected the moment nothing in Python still points at them, even
        # while a widget is actively displaying one.
        self._card_icon_images = [
            ctk.CTkImage(light_image=_make_dashboard_icon(), size=(26, 26)),
            ctk.CTkImage(light_image=_make_camera_icon(), size=(26, 26)),
        ]

        self.dashboard_button, self.dashboard_subtitle = self._choice_button(
            self.buttons_frame, "Dashboard", DASHBOARD_IDLE_SUBTITLE, self._open_dashboard,
            self._card_icon_images[0],
        )
        self.entry_agent_button, self.entry_agent_subtitle = self._choice_button(
            self.buttons_frame, "Entry Agent", ENTRY_AGENT_IDLE_SUBTITLE, self._open_entry_agent,
            self._card_icon_images[1],
        )
        self._relayout_buttons(side_by_side=False)

    def _relayout_buttons(self, side_by_side):
        self.dashboard_button.grid_forget()
        self.entry_agent_button.grid_forget()
        if side_by_side:
            self.buttons_frame.grid_columnconfigure(0, weight=1, uniform="card")
            self.buttons_frame.grid_columnconfigure(1, weight=1, uniform="card")
            self.dashboard_button.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
            self.entry_agent_button.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        else:
            self.buttons_frame.grid_columnconfigure(0, weight=1, uniform="")
            self.buttons_frame.grid_columnconfigure(1, weight=0, uniform="")
            self.dashboard_button.grid(row=0, column=0, sticky="ew", pady=(0, 16))
            self.entry_agent_button.grid(row=1, column=0, sticky="ew")

    def _choice_button(self, parent, title, subtitle, command, icon_image):
        """A clickable maroon card - a plain CTkFrame with a click binding
        rather than CTkButton, since CTkButton's single `text` can't render a
        bold title above a lighter subtitle. Same construction as the gate
        monitor's launcher card, so the two read as one app."""
        card = ctk.CTkFrame(parent, fg_color=MAROON, corner_radius=16, cursor="hand2")
        inner = ctk.CTkFrame(card, fg_color="transparent", cursor="hand2")
        inner.pack(fill="both", expand=True, padx=24, pady=18)

        header_row = ctk.CTkFrame(inner, fg_color="transparent", cursor="hand2")
        header_row.pack(fill="x")
        icon_label = ctk.CTkLabel(header_row, image=icon_image, text="", cursor="hand2")
        icon_label.pack(side="left", padx=(0, 10))
        title_label = ctk.CTkLabel(
            header_row, text=title, font=(FONT, 18, "bold"), text_color="white", anchor="w", cursor="hand2"
        )
        title_label.pack(side="left", fill="x", expand=True)
        # A plain "opens something" affordance hint, not a functional control
        # of its own - the whole card is already clickable.
        chevron_label = ctk.CTkLabel(
            header_row, text="›", font=(FONT, 20, "bold"), text_color=MAROON_LIGHT, cursor="hand2"
        )
        chevron_label.pack(side="right")

        subtitle_label = ctk.CTkLabel(
            inner, text=subtitle, font=(FONT, 12), text_color=MAROON_LIGHT,
            anchor="w", cursor="hand2", justify="left",
            # Fixed rather than recomputed per resize - a card is never
            # narrower than roughly this in either the stacked or side-by-
            # side layout, and wrapping a bit early in the wide stacked case
            # is a harmless cosmetic tradeoff for not overflowing the
            # narrowest side-by-side case.
            wraplength=280,
        )
        subtitle_label.pack(fill="x", pady=(10, 0))

        animator = _HoverAnimator(card, MAROON, MAROON_DARK)
        self._hover_animators.append(animator)  # keep a reference alive

        for widget in (card, inner, header_row, icon_label, title_label, chevron_label, subtitle_label):
            widget.bind("<Button-1>", lambda _event: command())
            widget.bind("<Enter>", animator.enter)
            widget.bind("<Leave>", animator.leave)
        return card, subtitle_label

    def _build_notices(self):
        """Startup-only, read-once information: what happened last time the
        gate monitor ran, and whether any NFC taps are still waiting to sync.
        Both come from files the entry-agent itself wrote, not from anything
        this launcher is tracking live - only shown if there's actually
        something to say, so a normal day-to-day launch (no queue backlog,
        first run of the day) doesn't grow an empty box."""
        lines = []
        last_session_line = _format_last_session_line(_read_last_session_summary())
        if last_session_line:
            lines.append((last_session_line, TEXT_MUTED))

        pending = _offline_queue_pending_count()
        if pending:
            noun = "tap" if pending == 1 else "taps"
            lines.append((
                f"⚠ {pending} NFC {noun} haven't synced to the server yet - "
                "they'll retry automatically once the entry-agent is running and online.",
                WARNING,
            ))

        if not lines:
            return
        wrap = ctk.CTkFrame(self.content, fg_color="transparent")
        wrap.pack(fill="x", padx=28, pady=(0, 8))
        for text, color in lines:
            ctk.CTkLabel(
                wrap, text=text, font=(FONT, 11), text_color=color,
                anchor="w", justify="left", wraplength=CONTENT_MAX_WIDTH - 56,
            ).pack(fill="x", anchor="w", pady=(2, 0))

    def _build_entry_agent_settings(self):
        """Gate, direction, and guard display name for the NEXT entry-agent
        launch - collapsed by default (same "Show X" convention as the log
        panel below) since most launches just reuse what was picked last
        time and don't need this open. Deliberately its own section rather
        than living inside the Entry Agent card itself: that card's whole
        surface is a single click target for opening it, and a text field or
        dropdown inside it would either eat clicks meant for the card or
        immediately trigger the card's own click handler - see _choice_button
        above, where every child widget is bound to the same "open" command."""
        wrap = ctk.CTkFrame(self.content, fg_color="transparent")
        wrap.pack(fill="x", padx=28, pady=(0, 8))

        header = ctk.CTkFrame(wrap, fg_color="transparent")
        header.pack(fill="x")
        self.settings_toggle = ctk.CTkButton(
            header, text="Entry Agent settings ▾", width=170, height=26, font=(FONT, 11),
            fg_color=CARD_BG, hover_color=BORDER, text_color=TEXT_SECONDARY,
            border_width=1, border_color=BORDER, command=self._toggle_settings_panel,
        )
        self.settings_toggle.pack(side="left")

        self.settings_panel = ctk.CTkFrame(wrap, fg_color=CARD_BG, corner_radius=12, border_width=1, border_color=BORDER)
        self._settings_panel_visible = False

        grid = ctk.CTkFrame(self.settings_panel, fg_color="transparent")
        grid.pack(fill="x", padx=16, pady=14)
        grid.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(grid, text="Gate", font=(FONT, 11), text_color=TEXT_SECONDARY).grid(
            row=0, column=0, sticky="w", pady=(0, 10)
        )
        self.gate_entry = ctk.CTkEntry(
            grid, font=(FONT, 12), fg_color=BG, border_color=BORDER, text_color=TEXT_PRIMARY,
        )
        self.gate_entry.insert(0, self.settings.get("gate_location", DEFAULT_GATE_LOCATION))
        self.gate_entry.grid(row=0, column=1, sticky="ew", padx=(10, 0), pady=(0, 10))
        self.gate_entry.bind("<FocusOut>", lambda _e: self._save_current_settings())

        ctk.CTkLabel(grid, text="Direction", font=(FONT, 11), text_color=TEXT_SECONDARY).grid(
            row=1, column=0, sticky="w", pady=(0, 10)
        )
        self.direction_selector = ctk.CTkSegmentedButton(
            grid, values=["Entry", "Exit"], font=(FONT, 11),
            selected_color=MAROON, selected_hover_color=MAROON_DARK,
            unselected_color=BG, text_color=TEXT_PRIMARY,
            command=lambda _value: self._save_current_settings(),
        )
        self.direction_selector.set(
            "Exit" if self.settings.get("direction", DEFAULT_DIRECTION) == "exit" else "Entry"
        )
        self.direction_selector.grid(row=1, column=1, sticky="w", padx=(10, 0), pady=(0, 10))

        ctk.CTkLabel(grid, text="Guard name", font=(FONT, 11), text_color=TEXT_SECONDARY).grid(
            row=2, column=0, sticky="w", pady=(0, 10)
        )
        self.guard_name_entry = ctk.CTkEntry(
            grid, font=(FONT, 12), fg_color=BG, border_color=BORDER, text_color=TEXT_PRIMARY,
            placeholder_text="Optional - shown on the gate monitor screen only",
        )
        self.guard_name_entry.insert(0, self.settings.get("officer_name", ""))
        self.guard_name_entry.grid(row=2, column=1, sticky="ew", padx=(10, 0), pady=(0, 10))
        self.guard_name_entry.bind("<FocusOut>", lambda _e: self._save_current_settings())

        self.auto_launch_var = tk.BooleanVar(value=bool(self.settings.get("auto_launch_entry_agent", False)))
        ctk.CTkCheckBox(
            grid, text="Automatically open the gate monitor when this launcher starts",
            font=(FONT, 11), text_color=TEXT_SECONDARY, variable=self.auto_launch_var,
            fg_color=MAROON, hover_color=MAROON_DARK,
            command=self._save_current_settings,
        ).grid(row=3, column=0, columnspan=2, sticky="w")

    def _toggle_settings_panel(self):
        if self._settings_panel_visible:
            self.settings_panel.pack_forget()
            self.settings_toggle.configure(text="Entry Agent settings ▾")
        else:
            self.settings_panel.pack(fill="x", pady=(8, 0))
            self.settings_toggle.configure(text="Entry Agent settings ▴")
        self._settings_panel_visible = not self._settings_panel_visible

    def _current_entry_agent_settings(self):
        """Reads the settings widgets directly rather than trusting whatever
        was last saved - covers the case where a field was edited but never
        blurred (no <FocusOut> fired yet) before "Entry Agent" was clicked."""
        return {
            "gate_location": self.gate_entry.get().strip() or DEFAULT_GATE_LOCATION,
            "direction": "exit" if self.direction_selector.get() == "Exit" else "entry",
            "officer_name": self.guard_name_entry.get().strip(),
            "auto_launch_entry_agent": bool(self.auto_launch_var.get()),
        }

    def _save_current_settings(self):
        self.settings = self._current_entry_agent_settings()
        _save_settings(self.settings)

    def _build_log_panel(self):
        wrap = ctk.CTkFrame(self.content, fg_color="transparent")
        wrap.pack(fill="both", expand=True, padx=28, pady=(16, 0))

        header = ctk.CTkFrame(wrap, fg_color="transparent")
        header.pack(fill="x")
        self.log_toggle = ctk.CTkButton(
            header, text="Show log ▾", width=90, height=26, font=(FONT, 11),
            fg_color=CARD_BG, hover_color=BORDER, text_color=TEXT_SECONDARY,
            border_width=1, border_color=BORDER, command=self._toggle_log,
        )
        self.log_toggle.pack(side="left")
        self.hint_label = ctk.CTkLabel(header, text="", font=(FONT, 11), text_color=TEXT_MUTED)
        self.hint_label.pack(side="left", padx=(12, 0))

        self.log_box = ctk.CTkTextbox(
            wrap, font=("Consolas", 10), fg_color=CARD_BG, text_color=TEXT_SECONDARY,
            border_width=1, border_color=BORDER, wrap="none",
        )
        self._log_visible = False

    def _build_footer(self):
        ctk.CTkFrame(self.content, fg_color=BORDER, height=1).pack(fill="x", pady=(16, 0))
        footer = ctk.CTkFrame(self.content, fg_color="transparent")
        footer.pack(fill="x", padx=28, pady=14)
        ctk.CTkLabel(
            footer, text=f"Quitting stops everything this window started.  ·  {self.app_version}",
            font=(FONT, 10), text_color=TEXT_MUTED,
        ).pack(side="left")
        ctk.CTkButton(
            footer, text="Quit", width=80, height=30, font=(FONT, 12),
            fg_color=CARD_BG, hover_color=BORDER, text_color=TEXT_PRIMARY,
            border_width=1, border_color=BORDER, command=self._handle_quit,
        ).pack(side="right")

    # ---- services -------------------------------------------------------

    def _start_backend(self):
        python = venv_python()
        if service_responds(HEALTH_URL):
            self._backend_external = True
            self._append_log("[launcher] a backend is already running on port 8000 - using that one")
            self.hint_label.configure(text="Using a backend that was already running.")
            return
        if not (BACKEND_DIR / "manage.py").exists():
            self._append_log(f"[launcher] backend not found at {BACKEND_DIR}")
            self._set_status("backend", "failed", "Backend missing")
            return
        self._append_log(f"[launcher] starting backend with {python}")
        # -u so Django's output reaches the log panel as it happens rather than
        # sitting in a pipe buffer until the process exits.
        self.backend.start([python, "-u", "manage.py", "runserver"], BACKEND_DIR)
        self._set_status("backend", "starting", "Backend starting")

    def _open_dashboard(self):
        if self.dashboard.is_running():
            self._launch_browser()
            return
        if service_responds(FALLBACK_DASHBOARD_URL):
            self._dashboard_external = True
            self._dashboard_url = FALLBACK_DASHBOARD_URL
            self._append_log("[launcher] a dashboard dev server is already running - using that one")
            self._launch_browser()
            return

        npm = npm_command()
        if npm is None:
            self._append_log("[launcher] npm not found on PATH - install Node.js to run the dashboard")
            messagebox.showerror(
                "Node.js not found",
                "npm isn't on your PATH, so the dashboard's dev server can't start.\n\n"
                "Install Node.js from https://nodejs.org, then reopen this launcher.",
            )
            return
        if not (DASHBOARD_DIR / "node_modules").exists():
            self._append_log("[launcher] dashboard/node_modules missing - run 'npm install' in dashboard/ first")
            messagebox.showerror(
                "Dashboard not installed",
                "dashboard/node_modules is missing.\n\n"
                "Open a terminal in the dashboard folder and run 'npm install' once, "
                "then reopen this launcher.",
            )
            return

        self._append_log("[launcher] starting dashboard dev server")
        self._dashboard_url = None
        self._browser_opened = False
        self._dashboard_started = True
        self.dashboard.start([npm, "run", "dev"], DASHBOARD_DIR)
        self._set_status("dashboard", "starting", "Dashboard starting")
        # Same wait, same treatment as the entry-agent - the dev server's first
        # start is several seconds too.
        self._start_spinner(
            "dashboard", self.dashboard_subtitle, "Starting the dev server, your browser will open shortly..."
        )
        # If Vite never prints a URL we can parse, open the conventional one
        # anyway rather than leaving the user staring at a button.
        self.root.after(20000, self._launch_browser_fallback)

    def _handle_dashboard_output(self, line):
        """Runs on the output-reader thread, so it does nothing but record the
        URL - Tk isn't safe to touch from another thread, even via after().
        The main loop notices the URL in _flush_log and opens the browser
        there, the same way the gate monitor hands work back to its Tk thread."""
        self._append_log(line)
        if self._dashboard_url is None:
            match = VITE_URL_PATTERN.search(line)
            if match:
                self._dashboard_url = match.group(0)

    def _launch_browser_fallback(self):
        if self.dashboard.is_running() and self._dashboard_url is None:
            self._dashboard_url = FALLBACK_DASHBOARD_URL
            self._launch_browser()

    def _launch_browser(self):
        url = self._dashboard_url or FALLBACK_DASHBOARD_URL
        self._browser_opened = True
        self._append_log(f"[launcher] opening {url}")
        # Stopping the spinner also writes the final subtitle, so this is the
        # one place the card's text settles.
        self._stop_spinner("dashboard", f"Running at {url} - click to reopen in your browser")
        if not self._spinning("dashboard"):
            self.dashboard_subtitle.configure(text=f"Running at {url} - click to reopen in your browser")
        webbrowser.open(url)

    def _open_entry_agent(self):
        if self.entry_agent.is_running():
            self._append_log("[launcher] entry-agent is already running")
            return
        if not (ENTRY_AGENT_DIR / "main.py").exists():
            self._append_log(f"[launcher] entry-agent not found at {ENTRY_AGENT_DIR}")
            return

        self._save_current_settings()
        self._run_entry_agent_preflight_checks()

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
        self.entry_agent.start([venv_python(), "-u", "main.py"], ENTRY_AGENT_DIR, env_overrides=env_overrides)
        self._set_status("entry-agent", "starting", "Entry agent starting")
        # Names what's actually taking the time, so the wait reads as work
        # rather than as the button having missed the click.
        self._start_spinner(
            "entry-agent", self.entry_agent_subtitle, "Starting the camera and opening the gate monitor..."
        )

    def _run_entry_agent_preflight_checks(self):
        """Fast, best-effort checks before opening the gate monitor -
        deliberately never blocks opening it: a false negative here (backend
        slow to answer, the NFC check itself failing) should never be able to
        stop a live demo. Camera presence is NOT checked here on purpose -
        the gate monitor already handles "no camera" gracefully on its own
        (placeholder + camera picker + hot-plug reconnect, see ui.py), and
        duplicating that check would mean importing cv2 into the launcher's
        own process just for this, adding a real startup-time cost for a
        case that's already covered downstream."""
        warnings = []
        if not service_responds(HEALTH_URL, timeout=1.5):
            warnings.append("The backend isn't responding yet - entry/exit logging won't work until it is.")
        if _detect_acr122u_reader() is False:
            warnings.append(
                "No ACR122U NFC reader was detected (best-effort check - some readers may not show up this way)."
            )
        if not warnings:
            return
        self._append_log("[launcher] pre-flight check: " + " | ".join(warnings))
        messagebox.showwarning(
            "Before you open the gate monitor",
            "\n\n".join(warnings) + "\n\nThe gate monitor will still open.",
        )

    def _handle_entry_agent_output(self, line):
        """Reader-thread side: record only. The Tk thread reacts in _flush_log."""
        self._append_log(line)
        if ENTRY_AGENT_READY_MARKER in line:
            self._entry_agent_ready = True

    # ---- loading animation ----------------------------------------------

    def _start_spinner(self, key, label, message):
        """Animate `label` while something starts up. Driven by Tk's after()
        rather than a thread - it's the main loop's own timer, so there's no
        cross-thread widget access to get wrong, and it stops dead if the
        window closes."""
        self._stop_spinner(key)
        self._spinners[key] = {
            "label": label,
            "message": message,
            "frame": 0,
            "job": None,
            "elapsed": 0,
        }
        self._tick_spinner(key)

    def _tick_spinner(self, key):
        state = self._spinners.get(key)
        if state is None:
            return
        frame = SPINNER_FRAMES[state["frame"] % len(SPINNER_FRAMES)]
        state["frame"] += 1
        state["elapsed"] += SPINNER_INTERVAL_MS
        state["label"].configure(text=f"{frame}    {state['message']}")
        if state["elapsed"] >= STARTUP_TIMEOUT_MS:
            self._stop_spinner(
                key, "Still not up after 45s - open the log below to see what happened"
            )
            return
        state["job"] = self.root.after(SPINNER_INTERVAL_MS, lambda: self._tick_spinner(key))

    def _stop_spinner(self, key, final_text=None):
        state = self._spinners.pop(key, None)
        if state is None:
            return
        if state["job"] is not None:
            self.root.after_cancel(state["job"])
        if final_text is not None:
            state["label"].configure(text=final_text)

    def _spinning(self, key):
        return key in self._spinners

    # ---- status ---------------------------------------------------------

    def _set_status(self, key, state, text):
        colors = {"ok": SUCCESS, "starting": WARNING, "failed": DANGER, "idle": TEXT_MUTED}
        label, _default = self.status_labels[key]
        glyph = self.STATUS_GLYPHS.get(state, self.STATUS_GLYPHS["idle"])
        label.configure(text=self._pad(f"{glyph}  {text}"), text_color=colors.get(state, TEXT_MUTED))

    def _poll_health(self):
        """Backend liveness plus a liveness check on each child process, so a
        service that died (MySQL down, a port already taken) turns red here
        instead of just never becoming ready."""
        if service_responds(HEALTH_URL, timeout=1.5):
            if not self._backend_ok:
                self._append_log("[launcher] backend is up")
            self._backend_ok = True
            self._set_status(
                "backend", "ok", "Backend ready (already running)" if self._backend_external else "Backend ready"
            )
        elif self.backend.is_running():
            self._backend_ok = False
            self._set_status("backend", "starting", "Backend starting")
        else:
            self._backend_ok = False
            self._set_status("backend", "failed", "Backend stopped")
            self.hint_label.configure(text="Backend stopped - open the log to see why")

        if self.dashboard.is_running():
            self._set_status("dashboard", "ok", "Dashboard running")
        elif self._dashboard_external and service_responds(FALLBACK_DASHBOARD_URL):
            self._set_status("dashboard", "ok", "Dashboard running (already running)")
        else:
            if self._spinning("dashboard"):
                self._stop_spinner("dashboard", "Failed to start - open the log below to see why")
                self._set_status("dashboard", "failed", "Dashboard failed")
                # Clearing this is what keeps the message on screen: leave it
                # set and the very next poll takes the reset branch below and
                # quietly overwrites the failure with the idle description.
                self._dashboard_started = False
            else:
                self._set_status("dashboard", "idle", "Dashboard")
                if self._dashboard_started:
                    self._dashboard_started = False
                    self._browser_opened = False
                    self._dashboard_url = None
                    self.dashboard_subtitle.configure(text=DASHBOARD_IDLE_SUBTITLE)

        if self.entry_agent.is_running():
            self._entry_agent_started = True
            self._set_status("entry-agent", "ok", "Entry agent running")
        elif self._spinning("entry-agent"):
            # It exited before ever signalling ready - almost always a camera
            # that wouldn't open or a bad .env, and the traceback is in the log.
            self._stop_spinner("entry-agent", "Failed to start - open the log below to see why")
            self._set_status("entry-agent", "failed", "Entry agent failed")
            # See the dashboard branch above - without this the next poll
            # overwrites the failure message with the idle description.
            self._entry_agent_started = False
        else:
            self._set_status("entry-agent", "idle", "Entry agent")
            if self._entry_agent_started:
                # Ran and was closed normally - put the card back to its
                # resting description rather than leaving "Gate monitor is
                # open" next to a window that isn't.
                self._entry_agent_started = False
                self.entry_agent_subtitle.configure(text=ENTRY_AGENT_IDLE_SUBTITLE)

        self.root.after(HEALTH_POLL_MS, self._poll_health)

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
        if self._dashboard_url and not self._browser_opened:
            self._launch_browser()
        if self._entry_agent_ready and self._spinning("entry-agent"):
            self._stop_spinner("entry-agent", "Gate monitor is open - check your taskbar if you don't see it")
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
            self.log_toggle.configure(text="Show log ▾")
            self._log_visible = False
        else:
            self.log_box.pack(fill="both", expand=True, pady=(8, 0))
            self.log_toggle.configure(text="Hide log ▴")
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
            # and an npm dev server left holding ports 8000/5173 with no window
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
    if not (ROOT / ".venv").exists():
        print(
            "WARNING: no .venv at the repo root - falling back to the interpreter "
            "running this script. See README.md if imports fail.",
            file=sys.stderr,
        )
    LauncherWindow().run()


if __name__ == "__main__":
    main()
