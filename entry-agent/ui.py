import io
import math
import os
import queue
import threading
import time
import tkinter as tk
import traceback
import tkinter.font as tkfont
import winsound
from datetime import datetime
from tkinter import messagebox

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageTk

# ---------------------------------------------------------------------------
# Design tokens - the redesign's whole palette is these 17 colors (see
# docs/design-brief.md). Status colors always travel with an icon and a word,
# never color alone.
# ---------------------------------------------------------------------------

INK = "#121416"
INK_600 = "#4B5157"
INK_400 = "#8A9097"
LINE = "#D9DCDF"
CANVAS = "#EEF0F2"
SURFACE = "#FFFFFF"
MAROON = "#7B1113"
MAROON_DEEP = "#4A0A0C"
BRASS = "#C89B3C"  # structural accent only (rules, active marks) - never text on white
VERIFIED = "#1E7B45"
VERIFIED_TINT = "#E3F2E9"
CAUTION = "#9A5B00"
CAUTION_TINT = "#FBEFD9"
DANGER = "#C62828"
DANGER_TINT = "#FBE4E4"
PROMPT = "#1D5FA8"
PROMPT_TINT = "#E2ECF7"

# Older names from before the redesign, kept as aliases onto the tokens
# above so any code still written against them gets the new palette.
MAROON_DARK = MAROON_DEEP
MAROON_LIGHT = LINE  # secondary text on maroon
BG = CANVAS
CARD_BG = SURFACE
BORDER = LINE
TEXT_PRIMARY = INK
TEXT_SECONDARY = INK_600
TEXT_MUTED = INK_400
SUCCESS = VERIFIED
WARNING = CAUTION
ACCENT = PROMPT
# A covered face is an instruction to the person ("uncover your face"), not
# an alarm - prompt blue, distinct from Unknown (caution) and spoof (danger).
OCCLUSION = PROMPT
PLACEHOLDER_AVATAR = CANVAS
SURFACE_ALT = CANVAS
VIDEO_BG = INK

FOCUS_CHECK_MS = 300
RESET_DELAY_MS = 8000
# Guard sign-in on: with nobody on duty the sign-in window opens by itself;
# "Not now" puts it away for this long, then it asks again.
SIGN_IN_REMIND_MS = 5 * 60 * 1000
# ~15 fps - smooth enough for a live view, and on a low-power laptop (the
# Intel N100 the system is presented on) it leaves CPU for the face AI.
VIDEO_REFRESH_MS = 66

ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
ICON_PATH = os.path.join(ASSETS_DIR, "icon.png")
# A proper multi-resolution .ico, generated from icon.png (same EVSU seal) -
# see _apply_icon's docstring for why this exists alongside the PNG.
ICON_ICO_PATH = os.path.join(ASSETS_DIR, "icon.ico")
FONTS_DIR = os.path.join(ASSETS_DIR, "fonts")

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")


# ---------------------------------------------------------------------------
# Fonts - bundled, loaded privately for this process only (nothing gets
# installed system-wide). Tk on Windows can't pick a width or weight out of a
# variable font, so each Archivo width/weight the design uses is its own
# static single-face family (see assets/fonts/README.md). Every family falls
# back to a stock Windows font if its file is missing, so a broken asset
# folder degrades the look, never the app.
# ---------------------------------------------------------------------------


def _load_bundled_fonts():
    loaded = set()
    if not os.path.isdir(FONTS_DIR):
        return loaded
    for filename in sorted(os.listdir(FONTS_DIR)):
        if not filename.lower().endswith(".ttf"):
            continue
        try:
            if ctk.FontManager.load_font(os.path.join(FONTS_DIR, filename)):
                loaded.add(filename)
        except Exception:
            pass
    return loaded


_LOADED_FONTS = _load_bundled_fonts()


def _family(filename, family, fallback):
    return family if filename in _LOADED_FONTS else fallback


# Body text - Atkinson Hyperlegible Next (regular + a real bold face).
FONT = _family("SecureTap-Text-Regular.ttf", "SecureTap Text", "Segoe UI")
# Data - IBM Plex Mono.
FONT_MONO = _family("IBMPlexMono-Regular.ttf", "IBM Plex Mono", "Consolas")
FONT_MONO_MEDIUM = _family("IBMPlexMono-Medium.ttf", "IBM Plex Mono Medium", "Consolas")
FONT_MONO_SEMIBOLD = _family("IBMPlexMono-SemiBold.ttf", "IBM Plex Mono SemiBold", "Consolas")
# Display - Archivo at three widths: condensed (75%) for eyebrow labels and
# status words, semi-expanded (112.5%) for titles, expanded (125%) for the
# big numbers. Always used with weight "normal" - the weight is in the face.
COND_BOLD = _family("SecureTap-Cond-Bold.ttf", "SecureTap Cond Bold", "Segoe UI Semibold")
COND_HEAVY = _family("SecureTap-Cond-Heavy.ttf", "SecureTap Cond Heavy", "Segoe UI Black")
SEMI_BOLD = _family("SecureTap-Semi-Bold.ttf", "SecureTap Semi Bold", "Segoe UI Semibold")
SEMI_HEAVY = _family("SecureTap-Semi-Heavy.ttf", "SecureTap Semi Heavy", "Segoe UI Black")
WIDE_BOLD = _family("SecureTap-Wide-Bold.ttf", "SecureTap Wide Bold", "Segoe UI Semibold")
WIDE_HEAVY = _family("SecureTap-Wide-Heavy.ttf", "SecureTap Wide Heavy", "Segoe UI Black")
WIDE_BLACK = _family("SecureTap-Wide-Black.ttf", "SecureTap Wide Black", "Segoe UI Black")
# Icons - Phosphor's icon font; codepoints from its style.css.
ICON_FONT = _family("Phosphor.ttf", "Phosphor", None)
ICON_FONT_BOLD = _family("Phosphor-Bold.ttf", "Phosphor-Bold", None)

ICONS = {
    "sign-in": "\ue428",
    "sign-out": "\ue42a",
    "user-circle-dashed": "\uec36",
    "warning-octagon": "\ue4e4",
    "warning": "\ue4e0",
    "users": "\ue4d6",
    "users-three": "\ue68e",
    "identification-card": "\ue2c8",
    "hand-palm": "\ue57e",
    "circle-notch": "\ueb44",
    "check-circle": "\ue184",
    "x-circle": "\ue4f8",
    "x": "\ue4f6",
    "cloud-slash": "\ue1b6",
    "video-camera-slash": "\ue4dc",
    "speaker-high": "\ue44a",
    "clock-counter-clockwise": "\ue1a0",
    "user-focus": "\ue6fc",
    # Student display (student_display.py) and its toggle in the status bar
    "monitor": "\ue32e",
    "monitor-arrow-up": "\ue58a",
    "scan-smiley": "\uebb4",
    "hand": "\ue298",
    # Launcher (launcher.py)
    "squares-four": "\ue464",
    "video-camera": "\ue4da",
    "caret-right": "\ue13a",
    "caret-down": "\ue136",
    "circle": "\ue18a",
    "power": "\ue3da",
    "plugs": "\ueb56",
    "check": "\ue182",
    "arrow-right": "\ue06c",
    "lock-simple": "\ue308",
}


def _icon(name, bold=True):
    """The glyph for one Phosphor icon, or "" when the icon font didn't load
    (so a label just shows its words instead of a missing-glyph box)."""
    family = ICON_FONT_BOLD if bold else ICON_FONT
    return ICONS.get(name, "") if family else ""


def _pil_font(filename, size, fallback="segoeuib.ttf"):
    try:
        return ImageFont.truetype(os.path.join(FONTS_DIR, filename), size=size)
    except Exception:
        try:
            return ImageFont.truetype(os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", fallback), size=size)
        except Exception:
            return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------


def set_app_user_model_id(app_id):
    """Windows groups a running process's TASKBAR button (and its icon) by
    the process's "App User Model ID" - left unset, a plain `python.exe`
    process falls back to sharing python.exe's own generic icon there,
    no matter what iconphoto()/iconbitmap() set on the window itself (the
    title bar and Alt+Tab switcher are usually fine either way; it's
    specifically the taskbar button that's affected). Has to run before the
    first window is created - call this as the very first thing in each
    entry point (see main.py/launcher.py). Windows-only and silently a no-op
    everywhere else."""
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass


def fit_to_screen(window, width, height, min_width, min_height):
    """Sizes a CTk window to width x height (logical px), shrunk to fit the
    screen's height above the taskbar, and centers it near the top. A 1080p
    laptop at 150% Windows scaling is only ~720 logical px tall - a fixed
    760px window put its bottom bar (and the button on it) off the screen."""
    scale = ctk.ScalingTracker.get_window_scaling(window)
    screen_width = window.winfo_screenwidth() / scale
    screen_height = window.winfo_screenheight() / scale
    height = int(min(height, screen_height - 90))
    width = int(min(width, screen_width - 40))
    window.minsize(min(min_width, width), min(min_height, height))
    # CTk scales the size but not the position, so the offsets are real px.
    x = max(0, round((screen_width - width) / 2 * scale))
    y = round(16 * scale)
    window.geometry(f"{width}x{height}+{x}+{y}")


def _apply_icon(window):
    """CustomTkinter windows still use Tk's iconphoto under the hood - a PNG
    works directly there (unlike iconbitmap, which specifically wants a
    Windows .ico). Sets both: iconbitmap is the one Windows' taskbar button
    actually reads reliably, iconphoto covers the title bar/Alt+Tab. Each is
    independently non-fatal if its asset is missing/fails to load, so one
    format issue can't cost the window its icon entirely. See
    set_app_user_model_id above for the other half of why a taskbar icon can
    still show wrong even with both of these set."""
    try:
        window.iconbitmap(ICON_ICO_PATH)
    except Exception:
        pass
    try:
        icon = ImageTk.PhotoImage(Image.open(ICON_PATH))
        window.iconphoto(True, icon)
        window._icon_ref = icon  # keep a reference or Tk garbage-collects it
    except Exception:
        pass


def _avatar_image(raw_bytes, size):
    """Crops/resizes raw_bytes to a plain size x size square photo, or None
    if there's no photo to show."""
    if not raw_bytes:
        return None
    try:
        source = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
        return ImageOps.fit(source, (size, size), Image.LANCZOS)
    except Exception:
        return None


def _initials_avatar(name, size, bg=CANVAS, fg=INK_600):
    """A square initials tile - the placeholder when there's no photo on
    file. Square like a real photo (3px corners), so the log reads as one
    column of pictures either way."""
    initials = "".join(part[0] for part in (name or "?").split()[:2]).upper() or "?"
    image = Image.new("RGBA", (size, size), bg)
    draw = ImageDraw.Draw(image)
    font = _pil_font("SecureTap-Semi-Bold.ttf", max(8, int(size * 0.32)))
    bbox = draw.textbbox((0, 0), initials, font=font)
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((size - text_w) / 2 - bbox[0], (size - text_h) / 2 - bbox[1]), initials, fill=fg, font=font)
    return image


def _glyph_tile(size, bg, icon_name, fg, scale=0.55, bold=False):
    """A square tile with one Phosphor icon centered on it - the thumbnail
    for an event with no face photo (unknown crop, rejected card, ...)."""
    image = Image.new("RGBA", (size, size), bg)
    glyph = _icon(icon_name, bold=bold)
    if glyph:
        draw = ImageDraw.Draw(image)
        font = _pil_font("Phosphor-Bold.ttf" if bold else "Phosphor.ttf", max(8, int(size * scale)))
        bbox = draw.textbbox((0, 0), glyph, font=font)
        glyph_w, glyph_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(((size - glyph_w) / 2 - bbox[0], (size - glyph_h) / 2 - bbox[1]), glyph, fill=fg, font=font)
    return image


def _scaled_size(source_size, target_size):
    """Fits source_size into target_size preserving aspect ratio, scaling up
    or down - unlike Image.thumbnail(), this scales up too, so a preview
    actually grows to fill its area instead of staying pinned at the
    source's native resolution."""
    source_width, source_height = source_size
    target_width, target_height = target_size
    if source_width <= 0 or source_height <= 0 or target_width <= 0 or target_height <= 0:
        return max(1, target_width), max(1, target_height)
    scale = min(target_width / source_width, target_height / source_height)
    return max(1, int(source_width * scale)), max(1, int(source_height * scale))


def _round_corners(image, radius):
    """Masks image's corners to transparent. A plain tk.Canvas draws a flat
    rectangle no matter what, so the video panel's rounding is baked into
    the one composited image it shows each frame (see _compose_panel) - an
    RGBA PhotoImage's alpha renders correctly on a Canvas, so the page
    background simply shows through the cut corners."""
    image = image.convert("RGBA")
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, image.size[0] - 1, image.size[1] - 1), radius=radius, fill=255
    )
    image.putalpha(mask)
    return image


def _rounded_rect_points(x0, y0, x1, y1, radius, steps_per_corner=12):
    """Points for a rounded rectangle as a Canvas polygon - each corner a
    real quarter-circle arc (steps_per_corner points), not the 2-points-per-
    corner + smooth=True spline Tk would otherwise approximate it with, which
    visibly undershoots the requested radius."""
    radius = min(radius, (x1 - x0) / 2, (y1 - y0) / 2)
    corners = (
        (x1 - radius, y0 + radius, -90, 0),    # top-right
        (x1 - radius, y1 - radius, 0, 90),     # bottom-right
        (x0 + radius, y1 - radius, 90, 180),   # bottom-left
        (x0 + radius, y0 + radius, 180, 270),  # top-left
    )
    points = []
    for cx, cy, start_deg, end_deg in corners:
        for step in range(steps_per_corner + 1):
            angle = math.radians(start_deg + (end_deg - start_deg) * step / steps_per_corner)
            points.append(cx + radius * math.cos(angle))
            points.append(cy + radius * math.sin(angle))
    return points


def _play_alert_sound():
    """A short alternating-tone alarm for an unrecognized face. winsound.Beep
    blocks its calling thread for the full duration, so this always runs on
    a throwaway daemon thread rather than the Tk main thread - otherwise the
    whole UI would freeze for the length of the alarm."""

    def _beep():
        try:
            for _ in range(2):
                winsound.Beep(1200, 180)
                winsound.Beep(900, 180)
        except RuntimeError:
            pass  # no audio device on this machine - never worth blocking the guard's UI over

    threading.Thread(target=_beep, daemon=True).start()


def _ellipsize(font, text, max_px):
    """Trims text with a trailing ellipsis until it fits max_px as measured
    in `font` - Tk labels never truncate on their own, they just clip or
    push their neighbors out."""
    if max_px <= 0 or font.measure(text) <= max_px:
        return text
    while text and font.measure(text + "…") > max_px:
        text = text[:-1]
    return text.rstrip() + "…"




# ---------------------------------------------------------------------------
# The gate monitor - the entry-agent's only window
# ---------------------------------------------------------------------------

# The redesign draws the gate monitor as a 1920x1080 screen, and every size in
# this file (fonts, paddings, the profiles below) is in that design's pixels.
# GateMonitorWindow fits that design to the actual screen by setting CTk's
# widget scaling (see _fit_design_to_screen) - so on a 1080p monitor the
# layout comes out at the design's proportions whatever Windows' display
# scaling is set to, instead of every element growing 25% at 125% scaling.
DESIGN_WIDTH, DESIGN_HEIGHT = 1920, 1080
# Floor for that fit, so a small laptop screen shrinks the layout but never
# to the point of unreadable text.
MIN_DESIGN_FIT = 0.6

# Size profile, in design pixels: the full 1920x1080 design ("WIDE") and its
# narrow 1100x700-window variant ("NARROW"). Every size in between is
# interpolated from the available width, so an un-maximized window still
# fits instead of overflowing at the full-screen sizes.
NARROW_SIZES = {
    "header": 84, "pad": 26, "seal": 44, "title": 28, "clock": 28, "badge": 22,
    "hero": 56, "stat": 44, "stat_top": 16, "col_gap": 26, "strip": 52, "banner_title": 22,
    "banner_sub": 16, "scan": 112, "scan_label": 150, "photo": 64, "feat_thumb": 72,
    "feat_name": 28, "feat_conf": 28, "feat_pad": 10, "band": 40, "feat_word": 18,
    "row_name": 20, "row_word": 16, "tab": 16, "tile": 56, "tap_title": 24,
}
WIDE_SIZES = {
    "header": 112, "pad": 42, "seal": 56, "title": 44, "clock": 44, "badge": 28,
    "hero": 72, "stat": 56, "stat_top": 26, "col_gap": 42, "strip": 64, "banner_title": 28,
    "banner_sub": 20, "scan": 150, "scan_label": 190, "photo": 76, "feat_thumb": 112,
    "feat_name": 36, "feat_conf": 44, "feat_pad": 20, "band": 48, "feat_word": 22,
    "row_name": 28, "row_word": 20, "tab": 20, "tile": 64, "tap_title": 28,
}


def _size_profile(design_width):
    t = max(0.0, min(1.0, (design_width - 1100) / (DESIGN_WIDTH - 1100)))
    return {key: round(NARROW_SIZES[key] + (WIDE_SIZES[key] - NARROW_SIZES[key]) * t) for key in NARROW_SIZES}


def _screen_work_area(window):
    """Real-pixel (width, height) of the usable screen, excluding the
    taskbar. Tk's own winfo_screenwidth() reports DPI-scaled units, not real
    pixels, so ask Windows directly; fall back to Tk's numbers elsewhere."""
    try:
        import ctypes
        from ctypes import wintypes
        rect = wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(rect), 0):  # SPI_GETWORKAREA
            return rect.right - rect.left, rect.bottom - rect.top
    except Exception:
        pass
    dpi = ctk.ScalingTracker.get_window_scaling(window)
    return round(window.winfo_screenwidth() * dpi), round(window.winfo_screenheight() * dpi)


# Everything that can land in the live log, and how it reads there: status
# color, the tint behind a non-routine row, its icon, and its word. `sec`
# marks the security-relevant kinds that get a colored bar and tint in the
# list - a routine pass stays plain white.
LOG_KINDS = {
    "entry": {"color": VERIFIED, "icon": "sign-in", "word": "ENTRY"},
    "card": {"color": VERIFIED, "icon": "identification-card", "word": "CARD"},
    "unknown": {"color": CAUTION, "tint": CAUTION_TINT, "icon": "user-circle-dashed", "word": "UNKNOWN", "sec": True},
    "spoof": {"color": DANGER, "tint": DANGER_TINT, "icon": "warning-octagon", "word": "SPOOF", "sec": True},
    "covered": {"color": PROMPT, "tint": PROMPT_TINT, "icon": "hand-palm", "word": "COVERED", "sec": True},
    "card_rejected": {"color": DANGER, "tint": DANGER_TINT, "icon": "identification-card", "word": "CARD REJECTED", "sec": True},
    "card_offline": {"color": CAUTION, "tint": CAUTION_TINT, "icon": "cloud-slash", "word": "QUEUED", "sec": True},
}

# The alarm banner docked at the top of the video panel - only for the two
# events that also sound the alarm. A covered face deliberately gets no
# banner (and no sound): it's shown as a label on the face itself, so the
# live view is never blocked for something a person fixes by lowering a hand.
ALERTS = {
    "unknown": {
        "color": CAUTION, "icon": "user-circle-dashed", "title": "UNKNOWN PERSON",
        "sub": "Not recognized — check this person at the gate",
    },
    "spoof": {
        "color": DANGER, "icon": "warning-octagon", "title": "SPOOF SUSPECTED",
        "sub": "Photo or screen held to the camera — not a live face",
    },
    # Settings page: "Alert on repeated unknown faces".
    "repeated": {
        "color": CAUTION, "icon": "user-circle-dashed", "title": "SAME UNKNOWN PERSON AGAIN",
        "sub": "Seen again and again at this gate — check this person",
    },
}


class _KeyBurst:
    """Tells a card tap apart from a person typing into a text box. The card
    reader "types" its whole number in a few hundredths of a second - keys
    far closer together than any person types - so a fast burst of at least
    MIN_LENGTH characters ending in Enter is a card, wherever focus was."""

    GAP_SECONDS = 0.05
    MIN_LENGTH = 4

    def __init__(self, entry):
        self._entry = entry
        self._start = 0
        self._last = 0.0
        entry.bind("<KeyPress>", self._key, add=True)

    def _key(self, event):
        # Runs before the box inserts the character, so the burst starts at
        # the box's current length.
        if not (event.char and event.char.isprintable()):
            return
        now = time.monotonic()
        if now - self._last > self.GAP_SECONDS:
            self._start = len(self._entry.get())
        self._last = now

    def take_card(self):
        """On Enter: the card number, removed from the box, if what was just
        typed was a card burst - else None."""
        text = self._entry.get()
        if time.monotonic() - self._last > self.GAP_SECONDS * 3 or len(text) - self._start < self.MIN_LENGTH:
            return None
        card = text[self._start:].strip()
        self._entry.delete(self._start, "end")
        return card or None


class GateMonitorWindow:
    """The entry-agent's only window, owning both credentials at once - the
    continuous camera check and the NFC card reader - so a guard watches one
    screen instead of alt-tabbing between two.

    There is deliberately no menu in front of this. Picking "Entry Agent" in
    the system launcher (launcher.py at the repo root) should land on the
    scanners, the live log and the feed - not on a second screen asking again
    what the user already said. So this owns the CTk root itself, and closing
    it ends the process.

    Layout (top to bottom): a maroon header with a brass rule; a stats row
    led by one big "today" number; then the video panel (with the card
    scanner strip under it) beside the live log, 7:5; and a white status bar.
    The live log leads with the newest event as a large featured card, with
    everything earlier listed below it.

    Camera side: a CCTV-style monitoring view, not a one-person kiosk. The
    camera continuously watches a stream of people walking through - nobody
    stops or poses, and several faces can be recognized in the same frame.
    Every bounding box, name, and confidence percentage drawn on the feed
    comes straight from the backend's /api/identify response for that exact
    frame (via show_recognitions()), not a separate local detector - one
    source of truth for "is this a face" and "who is it", so the box
    position and the identity label can never disagree.

    Per-person duplicate suppression already happens server-side (a
    recognized person or a lingering unrecognized face reuses its existing
    log row within a cooldown window, see backend/logs/views.py) - this
    window just has to *notice* that via the `deduped` flag on each result
    and only count/log genuinely new events, or the live log and stats
    strip would restate the same person every ~0.2s scan cycle.

    Card side: the NFC reader is a cheap HID-keyboard-emulation module (not
    a PC/SC smart card reader) - tapping a card makes it "type" the card's
    ID followed by Enter into whatever has OS keyboard focus. So this window
    keeps a hidden, off-screen Entry widget focused at all times to catch
    that input. Every tap lands in the same live log as a face event, so the
    log stays the one place that answers "who came through this gate", no
    matter which credential they used.

    Tkinter must run on the main thread, so the scan loop and tap handlers
    (both off-thread) push updates through a thread-safe queue rather than
    touching widgets directly.
    """

    MIN_VIDEO_SIZE = (320, 240)
    VIDEO_PANEL_CORNER_RADIUS = 8
    # How long with no frame at all before the video panel gives up waiting
    # and shows "No camera connected" instead of just sitting blank - long
    # enough that a real webcam's normal startup delay never trips it.
    CAMERA_GRACE_SECONDS = 3.0
    MAX_LOG_ROWS = 60
    LIST_THUMB_SIZE = 48
    ALERT_DISPLAY_MS = 6000

    FAILURE_HEADLINES = {
        "not_registered": "Not registered",
        "deactivated": "Deactivated",
        "read_error": "Read error",
        "offline": "Offline",
        "server_error": "Server error",
        # A guard's own staff ID card (Settings -> "Guards sign in at the gate
        # monitor") that couldn't sign them in - the reason is shown below.
        "staff_sign_in_off": "Staff card",
        "staff_not_allowed": "Can't sign in here",
        "staff_replayed": "Staff card",
    }

    # (key, label, color, icon) - "today" is the hero number, the rest sit
    # beside it separated by hairlines. Covered faces are still counted
    # (self.stats["occlusion"], used for the last-session summary) but have
    # no counter on screen.
    STAT_SPECS = (
        ("entries", "ENTRIES", INK, "sign-in"),
        ("unknown", "UNKNOWN", CAUTION, "user-circle-dashed"),
        ("spoof", "SPOOF", DANGER, "warning-octagon"),
        ("in_frame", "IN FRAME", INK_600, "users"),
    )
    # The stats wrap onto a second row only when one row doesn't fit the
    # left column - see _on_left_configure.
    STATS_COLUMNS_WIDE = len(STAT_SPECS)
    STATS_COLUMNS_NARROW = 2

    def __init__(self, gate_location, direction, get_preview_frame, on_tap,
                 officer_name="", version="", on_close=None,
                 camera_options=None, on_camera_change=None, initial_camera_index=None,
                 video_fps=None, student_display_fps=None, on_sign_in=None, on_sign_out=None):
        self._get_preview_frame = get_preview_frame
        # Guard sign-in (Settings -> "Guards sign in at the gate monitor"):
        # on_sign_in(username, password) and on_sign_out(shift_id) are main.py's,
        # run off the Tk thread; the answers come back through
        # set_gate_sign_in() / show_sign_in_result().
        self._on_sign_in = on_sign_in
        self._on_sign_out = on_sign_out
        self._sign_in = {"enabled": False, "on_duty": None}
        self._sign_in_dialog = None
        # The pending "ask again" after Not now (see _dismiss_sign_in_dialog).
        self._sign_in_reminder = None
        # From the launcher's speed mode (config.py) - VIDEO_REFRESH_MS when
        # run without one.
        self._video_refresh_ms = round(1000 / video_fps) if video_fps else VIDEO_REFRESH_MS
        self._student_display_fps = student_display_fps
        self.gate_location = gate_location
        self.direction = direction
        self.on_tap = on_tap
        self.officer_name = officer_name
        self.version = version
        self._on_close = on_close
        self._closed = False
        # camera_options: [(index, label), ...] from camera.list_available_
        # cameras() - a laptop with more than one camera (or a webcam that
        # isn't at index 0) otherwise has no way to tell the entry-agent
        # which one to actually use; see _build_status_bar/_handle_camera_
        # picked. on_camera_change(index) is main.py's Camera.set_index,
        # called when the dropdown selection changes.
        self._camera_options = camera_options or []
        self._on_camera_change = on_camera_change
        self._initial_camera_index = initial_camera_index
        self._camera_choices = {}  # display string -> index, filled in below

        self._video_image = None
        self._latest_recognitions = []
        self._latest_image_size = (1, 1)
        # Drives the "No camera connected" placeholder in _update_video -
        # tracked as a timestamp rather than a sticky boolean so the same
        # logic covers never connected, disconnected mid-session, and
        # reconnected. Starts at window-creation time, not zero, so a real
        # camera's normal brief startup delay doesn't immediately read as
        # "already been silent too long".
        self._last_frame_at = time.monotonic()
        self._showing_no_camera = False
        # The visible feed area on the video canvas, (x0, y0, x1, y1) - set
        # by _compose_panel each frame; face boxes are clipped to it.
        self._feed_bounds = (0, 0, 1, 1)
        self._panel_bounds = None
        self._seen_log_ids = set()
        self._log_entries = []  # newest first
        self._log_photo_images = []  # keeps CTkImage refs alive for the log list
        self.stats = {"entries": 0, "unknown": 0, "spoof": 0, "occlusion": 0}
        self._alert = None  # {"kind": ..., "time": ..., "sub": ...} while the alarm banner shows
        # Settings page: "Alert on suspected fake face" - on until the
        # backend says otherwise (see set_alert_settings).
        self._alert_spoof = True
        self._alert_hide_job = None
        self._card_reset_job = None
        self._card_images = {}
        self._last_card_id = None
        self._font_cache = {}
        # The student-facing second screen, while it's open - see
        # student_display.py and _toggle_student_display.
        self._student = None

        # This IS the application window, not a child of some menu - the
        # entry-agent opens straight into the monitor. Being the CTk root (not
        # a CTkToplevel) is what makes closing it end the app, which is the
        # right behaviour when there's nothing behind it to return to.
        self.window = ctk.CTk()
        self.window.title(f"EVSU SecureTap - Gate monitor - {gate_location}")
        self.window.configure(fg_color=CANVAS)
        # CustomTkinter multiplies geometry by the display's DPI scaling, so
        # these are deliberately conservative logical sizes - on a 125%
        # display 1200x720 is already 1500x900 real pixels. The window opens
        # maximized anyway (see below); this is just the restore size.
        self.window.geometry("1200x720")
        self.window.minsize(820, 640)
        self.window.protocol("WM_DELETE_WINDOW", self._handle_close)
        self.window.bind("<Escape>", lambda _e: self._handle_close())
        _apply_icon(self.window)

        work_width = self._fit_design_to_screen()
        self._scale = ctk.ScalingTracker.get_widget_scaling(self.window)
        self._s = _size_profile(work_width / self._scale)

        self.window.grid_columnconfigure(0, weight=1)
        self.window.grid_rowconfigure(2, weight=1)

        self._build_header()
        self._build_main_area()
        self._build_status_bar()
        self._rebuild_log_list()
        self._show_card_waiting()

        # The reader types into whatever has OS keyboard focus, so park a
        # hidden field off-screen and keep it focused (see _keep_focus).
        self._card_input_var = tk.StringVar()
        self._card_input = tk.Entry(self.window, textvariable=self._card_input_var)
        self._card_input.place(x=-500, y=-500)
        self._card_input.bind("<Return>", self._handle_card_input)

        self._queue = queue.Queue()
        self.window.after(100, self._process_queue)
        self.window.after(FOCUS_CHECK_MS, self._keep_focus)
        self._tick_clock()
        # Open filling the screen - this is an operational display a guard
        # leaves up all shift, not a dialog. Deferred because CTk re-applies
        # its own window attributes shortly after construction, and "zoomed"
        # set inline gets clobbered by that. Windows-only state, so a
        # TclError elsewhere just leaves the restore geometry in place.
        self.window.after(300, self._maximize)
        if self._get_preview_frame:
            self.window.after(self._video_refresh_ms, self._update_video)

    def _fit_design_to_screen(self):
        """Scales every CTk widget so the 1920x1080 design fills the screen
        a maximized window gets (the work area minus the title bar), rather
        than rendering at design size times Windows' display scaling. Only
        this process is affected - the gate monitor is the entry-agent's
        sole window. Returns the work area's width in real pixels."""
        dpi = ctk.ScalingTracker.get_window_scaling(self.window)
        work_width, work_height = _screen_work_area(self.window)
        client_height = work_height - round(24 * dpi)  # a maximized window's title bar
        fit = max(MIN_DESIGN_FIT, min(work_width / DESIGN_WIDTH, client_height / DESIGN_HEIGHT))
        ctk.set_widget_scaling(fit / dpi)
        # Changing the scaling makes CTk pin the window's max size to its
        # current size for about a second (it restores it on a timer), which
        # would silently block the maximize scheduled in __init__ - release
        # that pin now. CTk's own timer later restores the proper min size.
        tk.Tk.maxsize(self.window, 100000, 100000)
        return work_width

    def _maximize(self):
        if self._closed:
            return
        try:
            self.window.state("zoomed")
        except tk.TclError:
            pass

    # ---- small builders -------------------------------------------------

    def _tkfont(self, family, px, weight="normal"):
        """A tkinter Font at `px` logical pixels - for measuring text and for
        drawing on the video canvas, which (unlike CTk widgets) doesn't scale
        its own fonts for the display's DPI."""
        key = (family, px, weight)
        if key not in self._font_cache:
            self._font_cache[key] = tkfont.Font(
                root=self.window, family=family, size=-max(1, round(px * self._scale)), weight=weight
            )
        return self._font_cache[key]

    def _px(self, logical):
        return round(logical * self._scale)

    @staticmethod
    def _label(parent, text="", font=None, color=INK, **kwargs):
        return ctk.CTkLabel(parent, text=text, font=font, text_color=color, **kwargs)

    def _icon_label(self, parent, name, size, color, bold=True, **kwargs):
        family = ICON_FONT_BOLD if bold else ICON_FONT
        return ctk.CTkLabel(
            parent, text=_icon(name, bold), font=(family or FONT, size), text_color=color, **kwargs
        )

    def _ctk_image(self, pil_image, size):
        return ctk.CTkImage(light_image=pil_image, dark_image=pil_image, size=(size, size))

    def _thumb(self, entry, size):
        """The picture for a log entry: the person's photo (or the actual
        cropped capture for an unknown/spoof/covered face), falling back to
        an initials tile or an icon tile when there's no photo to show.
        Rendered at the display's real pixel size so it stays crisp."""
        real = self._px(size)
        image = _avatar_image(entry.get("photo_bytes"), real)
        if image is None:
            kind = entry["kind"]
            if kind in ("unknown", "spoof"):
                image = _glyph_tile(real, INK, "user-circle-dashed", INK_400)
            elif kind == "covered":
                image = _glyph_tile(real, INK, "hand-palm", INK_400)
            elif kind == "card_rejected":
                image = _glyph_tile(real, DANGER, "identification-card", SURFACE, bold=True)
            elif kind == "card_offline":
                image = _glyph_tile(real, CAUTION, "cloud-slash", SURFACE, bold=True)
            else:
                image = _initials_avatar(entry.get("initials_seed") or entry.get("name"), real)
        image = _round_corners(image, self._px(3))
        return self._ctk_image(image, size)

    # ---- layout ---------------------------------------------------------

    def _build_header(self):
        s = self._s
        header = ctk.CTkFrame(self.window, fg_color=MAROON, corner_radius=0, height=s["header"])
        header.grid(row=0, column=0, sticky="ew")
        header.pack_propagate(False)
        self.header = header
        ctk.CTkFrame(self.window, fg_color=BRASS, corner_radius=0, height=3).grid(row=1, column=0, sticky="ew")

        try:
            seal = Image.open(ICON_PATH).convert("RGBA")
            self._seal_image = ctk.CTkImage(light_image=seal, dark_image=seal, size=(s["seal"], s["seal"]))
            ctk.CTkLabel(header, image=self._seal_image, text="").pack(side="left", padx=(s["pad"], 26))
        except Exception:
            pass  # a missing seal asset shouldn't stop the gate from opening

        wordmark = ctk.CTkFrame(header, fg_color="transparent")
        wordmark.pack(side="left", padx=(0, 26))
        self._label(wordmark, "EVSU", (WIDE_BLACK, 20), "white", height=22).pack(anchor="w")
        self._label(wordmark, "SecureTap", (SEMI_BOLD, 20), "white", height=22).pack(anchor="w")

        # Plain tk.Frame - a 1px CTkFrame draws nothing, which is why this
        # divider used to be missing.
        tk.Frame(header, bg=BRASS, width=max(1, self._px(1))).pack(side="left", fill="y", pady=26)

        # Right side packs right-to-left: the ENTRY pill (every gate checks
        # people coming in - exits aren't recorded), then the clock.
        badge = ctk.CTkFrame(header, fg_color=SURFACE, corner_radius=round(s["badge"] * 1.1))
        badge.pack(side="right", padx=(26, s["pad"]))
        self._icon_label(badge, "sign-in", round(s["badge"] * 1.0),
                         MAROON_DEEP).pack(side="left", padx=(18, 8), pady=8)
        self._label(badge, "ENTRY", (WIDE_HEAVY, s["badge"]), MAROON_DEEP).pack(
            side="left", padx=(0, 22), pady=8
        )

        clock = ctk.CTkFrame(header, fg_color="transparent")
        clock.pack(side="right")
        self.clock_label = self._label(clock, "", (FONT_MONO_MEDIUM, s["clock"]), "white", height=s["clock"] + 4)
        self.clock_label.pack(anchor="e")
        self.date_label = self._label(clock, "", (FONT_MONO, 14), "white", height=18)
        self.date_label.pack(anchor="e")

        title = ctk.CTkFrame(header, fg_color="transparent")
        title.pack(side="left", fill="x", expand=True, padx=(26, 26))
        self._title_full = f"{self.gate_location} — live monitoring"
        self._title_label = self._label(
            title, self._title_full, (SEMI_HEAVY, s["title"]), "white", anchor="w", height=s["title"] + 6,
        )
        self._title_label.pack(fill="x", anchor="w")
        self._label(title, "Face recognition + NFC card", (FONT, 16), "white", anchor="w", height=20).pack(
            fill="x", anchor="w"
        )
        self._title_frame = title
        self._title_width = None
        title.bind("<Configure>", self._fit_header_title)

    def _fit_header_title(self, _event=None):
        """Ellipsizes the gate title to whatever width is left between the
        wordmark and the clock, so a long gate name or a narrow window never
        runs the title underneath the clock."""
        width = self._title_frame.winfo_width()
        if width <= 1 or width == self._title_width:
            return
        self._title_width = width
        font = self._tkfont(SEMI_HEAVY, self._s["title"])
        self._title_label.configure(text=_ellipsize(font, self._title_full, width - self._px(4)))

    def _tick_clock(self):
        if self._closed:
            return
        now = datetime.now()
        self.clock_label.configure(text=now.strftime("%I:%M:%S %p"))
        self.date_label.configure(text=f"{now:%a} · {now:%b} {now.day}, {now.year}")
        self.window.after(1000, self._tick_clock)

    def _build_stats_strip(self, parent):
        """The stats row above the feed (left column only - the live log
        takes the full height of the right column): the "today" hero number,
        then one label + number per stat, each behind a hairline divider,
        all sitting on one baseline."""
        s = self._s
        self.stats_strip = ctk.CTkFrame(parent, fg_color="transparent")
        self.stats_strip.grid(row=0, column=0, sticky="ew", pady=(max(0, s["stat_top"] - 8), 16))

        hero = ctk.CTkFrame(self.stats_strip, fg_color="transparent")
        hero.grid(row=0, column=0, sticky="sw", padx=(0, 68))
        self._hero_frame = hero
        self._label(hero, "TODAY", (COND_BOLD, 14), INK_600, height=18).pack(anchor="w")
        self.stat_tiles = {}
        self.stat_tiles["today"] = self._label(hero, "0", (WIDE_HEAVY, s["hero"]), INK, height=round(s["hero"] * 0.95))
        self.stat_tiles["today"].pack(anchor="w")

        self._stat_tile_frames = []
        for key, label, color, icon in self.STAT_SPECS:
            tile = ctk.CTkFrame(self.stats_strip, fg_color="transparent")
            # Plain tk.Frame - a CTkFrame this thin draws nothing at all.
            tk.Frame(tile, bg=LINE, width=max(1, self._px(1))).pack(side="left", fill="y")
            body = ctk.CTkFrame(tile, fg_color="transparent")
            body.pack(side="left", anchor="s", padx=(26, 42))
            head = ctk.CTkFrame(body, fg_color="transparent")
            head.pack(anchor="w")
            self._icon_label(head, icon, 16, color, height=18).pack(side="left", padx=(0, 6))
            self._label(head, label, (COND_BOLD, 14), color, height=18).pack(side="left")
            value = self._label(body, "0", (WIDE_BOLD, s["stat"]), color, height=round(s["stat"] * 0.95))
            value.pack(anchor="w")
            self.stat_tiles[key] = value
            self._stat_tile_frames.append(tile)

        self._stats_columns = None  # forces the first _relayout_stats call to actually apply
        self._relayout_stats(self.STATS_COLUMNS_WIDE)

    def _relayout_stats(self, columns):
        if columns == self._stats_columns:
            return
        self._stats_columns = columns
        for tile in self._stat_tile_frames:
            tile.grid_forget()
        # Wrapped onto two rows, the hero number spans both so it stays
        # beside the stats instead of leaving a hole under itself.
        rows = -(-len(self._stat_tile_frames) // columns)
        self._hero_frame.grid_configure(rowspan=rows)
        for index, tile in enumerate(self._stat_tile_frames):
            row, column = divmod(index, columns)
            # "ns" so the divider runs the full height of the row.
            tile.grid(row=row, column=column + 1, sticky="nsw", pady=(0 if row == 0 else 10, 0))

    def _on_left_configure(self, event):
        # Wraps the stats onto two rows only when one row genuinely doesn't
        # fit the left column - measured from the stats' own requested
        # widths rather than a guessed window breakpoint, since the column's
        # width depends on the 7:5 split as much as on the window. (CTk
        # delivers this from the frame's internal canvas, so event.widget is
        # never the CTkFrame itself - no widget check here.) Measured once
        # the resize settles, from the column's real width - the event's own
        # width can be a stale intermediate during a maximize/restore.
        self.window.after_idle(self._check_stats_wrap)

    def _check_stats_wrap(self):
        if self._closed:
            return
        width = self._left_column.winfo_width()
        if width <= 1:
            return
        # The hero's right padding isn't part of its requested width.
        needed = (self._hero_frame.winfo_reqwidth() + self._px(68)
                  + sum(t.winfo_reqwidth() for t in self._stat_tile_frames))
        wrapped = width < needed
        self._relayout_stats(self.STATS_COLUMNS_NARROW if wrapped else self.STATS_COLUMNS_WIDE)

    def _build_main_area(self):
        """Two columns under the header, 7:5. Left: the stats row, the video
        feed (absorbing all spare height) and the card scanner strip. Right:
        the live log, running the full height from the header to the status
        bar - it's the running record, so it gets every row it can show."""
        s = self._s
        area = ctk.CTkFrame(self.window, fg_color="transparent")
        area.grid(row=2, column=0, sticky="nsew", padx=s["pad"], pady=(8, 16))
        area.grid_rowconfigure(0, weight=1)
        area.grid_columnconfigure(0, weight=7, uniform="main")
        area.grid_columnconfigure(1, weight=5, uniform="main")

        left = ctk.CTkFrame(area, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, s["col_gap"]))
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(1, weight=1)  # the feed absorbs all spare height
        self._left_column = left

        self._build_stats_strip(left)
        self._build_video_panel(left)
        self._build_card_panel(left)
        self._build_log_panel(area)
        left.bind("<Configure>", self._on_left_configure)

    def _build_video_panel(self, parent):
        # The canvas fills its cell edge-to-edge and paints the whole panel
        # itself - dark rounded body, the top strip (LIVE row or alarm
        # banner) and the feed - as ONE composited image each frame, masked
        # to the panel's corner radius (see _compose_panel). Overlay text is
        # drawn straight onto the canvas on top of that, never as CTkLabel
        # widgets: a widget can't show the live video behind it, so any
        # label over the feed would sit in its own flat-colored box.
        self.video_canvas = tk.Canvas(parent, bg=CANVAS, highlightthickness=0)
        self.video_canvas.grid(row=1, column=0, sticky="nsew")
        self._caption_text = "Starting camera…"

    def _build_card_panel(self, parent):
        """The compact card-scanner strip under the feed: a status bar on its
        left edge, a fixed "CARD SCANNER · Reader ready" label block, then a
        body that swaps between the tap prompt and the latest tap's result.
        The outer frame is filled with the status color and the white inner
        panel sits inset by the bar's width - CTk can't round just one side of
        a frame, so this is how the colored edge follows the rounded corners."""
        s = self._s
        self.card_panel = ctk.CTkFrame(parent, fg_color=LINE, corner_radius=8, height=s["scan"])
        self.card_panel.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        self.card_panel.grid_propagate(False)
        self.card_panel.grid_rowconfigure(0, weight=1)
        self.card_panel.grid_columnconfigure(0, weight=1)

        inner = ctk.CTkFrame(self.card_panel, fg_color=SURFACE, corner_radius=7)
        inner.grid(row=0, column=0, sticky="nsew", padx=(6, 1), pady=1)
        inner.grid_rowconfigure(0, weight=1)
        inner.grid_columnconfigure(2, weight=1)
        self.card_inner = inner

        label_block = ctk.CTkFrame(inner, fg_color="transparent", width=s["scan_label"])
        # Inset vertically so its square canvas can't paint over the
        # panel's rounded corners.
        label_block.grid(row=0, column=0, sticky="ns", padx=(14, 16), pady=8)
        label_block.grid_propagate(False)
        label_block.grid_rowconfigure((0, 3), weight=1)
        self._label(label_block, "CARD SCANNER", (COND_BOLD, 14), INK_600, anchor="w").grid(row=1, column=0, sticky="w")
        ready = ctk.CTkFrame(label_block, fg_color="transparent")
        ready.grid(row=2, column=0, sticky="w", pady=(4, 0))
        self._icon_label(ready, "check-circle", 16, VERIFIED).pack(side="left", padx=(0, 6))
        self.reader_label = self._label(ready, "Reader ready", (FONT, 14, "bold"), VERIFIED)
        self.reader_label.pack(side="left")

        ctk.CTkFrame(inner, fg_color=LINE, width=1, height=1, corner_radius=0).grid(row=0, column=1, sticky="ns")

        # The swapping body. Its fg changes to a tint for a rejected/queued
        # tap, so every widget inside is recolored explicitly on each render
        # (see _set_card_body_color) - CTk only pushes a new parent color
        # down one level.
        self.card_body = ctk.CTkFrame(inner, fg_color=SURFACE, corner_radius=0)
        self.card_body.grid(row=0, column=2, sticky="nsew", padx=(0, 6), pady=6)
        self.card_body.grid_rowconfigure(0, weight=1)
        self.card_body.grid_columnconfigure(1, weight=1)

        tile = s["tile"]
        self.card_tile = ctk.CTkLabel(self.card_body, text="", width=tile, height=tile, corner_radius=8)
        self.card_tile.grid(row=0, column=0, padx=(20, 20))

        text = ctk.CTkFrame(self.card_body, fg_color="transparent")
        text.grid(row=0, column=1, sticky="ew")
        self.card_text = text
        self.card_title = self._label(text, "", (SEMI_HEAVY, s["tap_title"]), INK, anchor="w")
        self.card_title.pack(fill="x", anchor="w")
        self.card_sub = self._label(text, "", (FONT, 16), INK_600, anchor="w")
        self.card_sub.pack(fill="x", anchor="w")
        self.card_ids = self._label(text, "", (FONT_MONO_MEDIUM, 14), INK_600, anchor="w")
        self.card_ids.pack(fill="x", anchor="w")
        # A distinguishing note (see users.models.Person.distinguishing_note)
        # only shows when this specific person has one on file.
        self.card_note = self._label(text, "", (FONT, 13, "bold"), CAUTION, anchor="w")

        right = ctk.CTkFrame(self.card_body, fg_color="transparent")
        right.grid(row=0, column=2, sticky="e", padx=(12, 20))
        self.card_right = right
        word_row = ctk.CTkFrame(right, fg_color="transparent")
        word_row.pack(anchor="e")
        self.card_word_icon = self._icon_label(word_row, "identification-card", 24, VERIFIED)
        self.card_word_icon.pack(side="left", padx=(0, 6))
        self.card_word = self._label(word_row, "", (COND_HEAVY, 22), VERIFIED)
        self.card_word.pack(side="left")
        self.card_word_row = word_row
        self.card_time = self._label(right, "", (FONT_MONO_MEDIUM, 16), INK_600, anchor="e")
        self.card_time.pack(anchor="e")

    def _build_log_panel(self, parent):
        s = self._s
        log = ctk.CTkFrame(parent, fg_color="transparent")
        log.grid(row=0, column=1, sticky="nsew")
        self._log_panel = log
        log.grid_columnconfigure(0, weight=1)
        log.grid_rowconfigure(4, weight=1)

        head = ctk.CTkFrame(log, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        head.grid_columnconfigure(0, weight=1)
        self._label(head, "Live log", (SEMI_BOLD, 20), INK, anchor="w").grid(row=0, column=0, sticky="w")
        self.log_count_label = self._label(
            head, f"Newest first · last {self.MAX_LOG_ROWS}", (FONT_MONO, 14), INK_600, anchor="e"
        )
        self.log_count_label.grid(row=0, column=1, sticky="e")

        # Featured (newest) event: a colored band with the event's word and
        # time, over a white body with the photo, name, ID and match score.
        # Band and body are two rounded frames overlapped with a square
        # filler strip hiding the seam, since CTk can't round only the top or
        # bottom corners of one frame.
        self._band_h = s["band"]
        # Tall enough for a name wrapped onto two lines plus the ID line
        # under it, not just the photo - long Filipino names wrap often.
        text_h = round(s["feat_name"] * 1.3 * 2 + 18)
        body_h = max(s["feat_thumb"], text_h) + 2 * s["feat_pad"]
        self.feat_card = ctk.CTkFrame(log, fg_color="transparent", corner_radius=0, height=self._band_h + body_h)
        self.feat_card.grid(row=1, column=0, sticky="ew")
        self.feat_band = ctk.CTkFrame(self.feat_card, fg_color=INK_600, corner_radius=8, height=self._band_h + 10)
        self.feat_band.place(x=0, y=0, relwidth=1)
        self.feat_body = ctk.CTkFrame(self.feat_card, fg_color=SURFACE, corner_radius=8, height=body_h)
        self.feat_body.place(x=0, y=self._band_h, relwidth=1)
        ctk.CTkFrame(self.feat_card, fg_color=SURFACE, corner_radius=0, height=10).place(
            x=0, y=self._band_h, relwidth=1
        )

        # Fixed heights: pack/grid would otherwise shrink these frames to
        # their contents and break the band/body overlap above.
        self.feat_band.pack_propagate(False)
        self.feat_body.grid_propagate(False)
        band_row = ctk.CTkFrame(self.feat_band, fg_color=INK_600, corner_radius=0, height=self._band_h - 4)
        band_row.pack(fill="x", padx=12, pady=(2, 0))
        band_row.pack_propagate(False)
        self.feat_band_row = band_row
        self.feat_icon = self._icon_label(band_row, "sign-in", s["feat_word"] + 4, "white", fg_color=INK_600)
        self.feat_icon.pack(side="left", padx=(8, 8))
        self.feat_word = self._label(band_row, "", (COND_HEAVY, s["feat_word"]), "white", fg_color=INK_600, anchor="w")
        self.feat_word.pack(side="left", fill="x", expand=True)
        self.feat_time = self._label(band_row, "", (FONT_MONO_MEDIUM, 16), "white", fg_color=INK_600)
        self.feat_time.pack(side="right", padx=(10, 8))

        self.feat_body.grid_columnconfigure(1, weight=1)
        self.feat_body.grid_rowconfigure(0, weight=1)
        self.feat_thumb = ctk.CTkLabel(self.feat_body, text="")
        self.feat_thumb.grid(row=0, column=0, padx=(18, 16), pady=s["feat_pad"])
        feat_text = ctk.CTkFrame(self.feat_body, fg_color="transparent")
        feat_text.grid(row=0, column=1, sticky="ew")
        self.feat_name = self._label(feat_text, "", (FONT, s["feat_name"], "bold"), INK, anchor="w", justify="left")
        self.feat_name.pack(fill="x", anchor="w")
        self.feat_id = self._label(feat_text, "", (FONT_MONO_MEDIUM, 16), INK_600, anchor="w")
        self.feat_id.pack(fill="x", anchor="w", pady=(6, 0))
        self.feat_match = ctk.CTkFrame(self.feat_body, fg_color="transparent")
        self.feat_match.grid(row=0, column=2, sticky="e", padx=(12, 18))
        self._label(self.feat_match, "MATCH", (COND_BOLD, 14), INK_600, anchor="e").pack(anchor="e")
        self.feat_conf = self._label(self.feat_match, "", (FONT_MONO_SEMIBOLD, s["feat_conf"]), INK, anchor="e")
        self.feat_conf.pack(anchor="e")

        self.feat_empty = self._label(
            log, "Waiting for the first scan or tap…", (FONT, 16), INK_600, anchor="w"
        )

        self._label(log, "EARLIER", (COND_BOLD, 14), INK_600, anchor="w").grid(
            row=3, column=0, sticky="w", pady=(20, 6)
        )
        self.log_list = ctk.CTkScrollableFrame(
            log, fg_color=SURFACE, corner_radius=8, border_width=1, border_color=LINE,
            # Scrollbar blends into the card until hovered (the design shows
            # none); the list still scrolls with the mouse wheel.
            scrollbar_button_color=SURFACE, scrollbar_button_hover_color=LINE,
        )
        self.log_list.grid(row=4, column=0, sticky="nsew")
        self.log_list.grid_columnconfigure(0, weight=1)
        # Names/meta lines are ellipsized to the list's width at build time,
        # so re-lay the log out (debounced) when that width actually changes.
        self._log_width = None
        self._log_relayout_job = None
        log.bind("<Configure>", self._on_log_configure)

    def _on_log_configure(self, event):
        if self._log_width is not None and abs(event.width - self._log_width) < 8:
            return
        self._log_width = event.width
        if self._log_relayout_job:
            self.window.after_cancel(self._log_relayout_job)
        self._log_relayout_job = self.window.after(150, self._relayout_log)

    def _relayout_log(self):
        self._log_relayout_job = None
        if not self._closed:
            self._rebuild_log_list()

    def _build_status_bar(self):
        tk.Frame(self.window, bg=LINE, height=max(1, self._px(1))).grid(row=3, column=0, sticky="ew")
        bar = ctk.CTkFrame(self.window, fg_color=SURFACE, corner_radius=0, height=48)
        bar.grid(row=4, column=0, sticky="ew")
        inner = ctk.CTkFrame(bar, fg_color="transparent")
        inner.pack(fill="x", padx=self._s["pad"], pady=6)

        threshold = ctk.CTkFrame(inner, fg_color="transparent")
        threshold.pack(side="left")
        self._label(threshold, "Recognition threshold", (FONT, 14), INK_600).pack(side="left", padx=(0, 6))
        self.threshold_label = self._label(threshold, "—", (FONT_MONO_MEDIUM, 14), INK)
        self.threshold_label.pack(side="left")

        # Camera health - a camera that stopped responding is precisely what
        # a guard needs to notice, and the feed going still doesn't always
        # look different from an empty gate.
        camera = ctk.CTkFrame(inner, fg_color="transparent")
        camera.pack(side="left", padx=(26, 0))
        self.camera_icon = self._icon_label(camera, "check-circle", 16, VERIFIED)
        self.camera_icon.pack(side="left", padx=(0, 6))
        self.camera_label = self._label(camera, "Camera OK", (FONT, 14, "bold"), VERIFIED)
        self.camera_label.pack(side="left")
        self._build_camera_picker(inner)
        self._build_student_toggle(inner)

        identity = " · ".join(part for part in (self.officer_name, self.version) if part)
        self._identity_label = None
        if identity:
            self._identity_label = self._label(inner, identity, (FONT, 14), INK_600)
            self._identity_label.pack(side="left", padx=(26, 0))

        # Who's on duty - replaces the typed-in guard name above while guard
        # sign-in is switched on (see _render_gate_sign_in). Clicking it signs
        # in or out.
        self._duty = ctk.CTkFrame(inner, fg_color="transparent", cursor="hand2")
        self._duty_icon = self._icon_label(self._duty, "user-focus", 16, INK_600, cursor="hand2")
        self._duty_icon.pack(side="left", padx=(0, 6))
        self._duty_label = self._label(self._duty, "", (FONT, 14, "bold"), INK, cursor="hand2")
        self._duty_label.pack(side="left")
        self._duty_action = self._label(self._duty, "", (FONT, 14, "bold", "underline"), MAROON, cursor="hand2")
        self._duty_action.pack(side="left", padx=(10, 0))
        for widget in (self._duty, self._duty_icon, self._duty_label, self._duty_action):
            widget.bind("<Button-1>", lambda _e: self._duty_clicked())

        # Right side, packed right-to-left: sync, queue, backend.
        sync = ctk.CTkFrame(inner, fg_color="transparent")
        sync.pack(side="right")
        self.sync_icon = self._icon_label(sync, "check-circle", 16, VERIFIED)
        self.sync_icon.pack(side="left", padx=(0, 6))
        self.sync_label = self._label(sync, "Synced", (FONT, 14, "bold"), VERIFIED)
        self.sync_label.pack(side="left")
        self.queue_label = self._label(inner, "Queue 0", (FONT_MONO_MEDIUM, 14), INK_600)
        self.queue_label.pack(side="right", padx=(0, 26))
        backend = ctk.CTkFrame(inner, fg_color="transparent")
        backend.pack(side="right", padx=(0, 26))
        self.backend_icon = self._icon_label(backend, "check-circle", 16, VERIFIED)
        self.backend_icon.pack(side="left", padx=(0, 6))
        self.backend_label = self._label(backend, "Backend OK", (FONT, 14, "bold"), VERIFIED)
        self.backend_label.pack(side="left")

    def _build_camera_picker(self, bar):
        """A laptop with more than one camera (or a desktop where the
        webcam just isn't at index 0) has no other way to tell the
        entry-agent which one to actually use - without this, the video
        panel can show "No camera connected" even though a perfectly good
        camera IS plugged in, just not the one Camera happened to try first.
        Values are "index: label" strings (e.g. "0: Logitech BRIO") so the
        index survives round-tripping through CTkOptionMenu's plain-string
        API - self._camera_choices maps each value back to its index."""
        display_values = [f"{index}: {label}" for index, label in self._camera_options]
        if not display_values:
            display_values = ["No camera found"]
        self._camera_choices = {
            value: index for value, (index, _label) in zip(display_values, self._camera_options)
        }

        default_value = next(
            (value for value, (index, _label) in zip(display_values, self._camera_options)
             if index == self._initial_camera_index),
            display_values[0],
        )

        self.camera_picker = ctk.CTkOptionMenu(
            bar, values=display_values, command=self._handle_camera_picked,
            width=210, height=30, corner_radius=3, font=(FONT_MONO, 13), dropdown_font=(FONT_MONO, 13),
            fg_color=CANVAS, text_color=INK, button_color=CANVAS, button_hover_color=LINE,
            dropdown_fg_color=SURFACE, dropdown_hover_color=CANVAS, dropdown_text_color=INK,
        )
        self.camera_picker.set(default_value)
        self.camera_picker.pack(side="left", padx=(12, 0))
        if not self._camera_options:
            # Nothing to switch to - still shown (so it's obvious the app
            # looked and found none), just not interactive.
            self.camera_picker.configure(state="disabled")

    def _handle_camera_picked(self, selected_value):
        index = self._camera_choices.get(selected_value)
        if index is not None and self._on_camera_change:
            self._on_camera_change(index)

    def _build_student_toggle(self, bar):
        """"Open student display" / "Student display open · Close" - opens
        the student-facing second screen (student_display.py) or closes it.
        A frame with an icon and a word rather than a CTkButton, which can't
        mix the icon font with the text font in one label."""
        # A 1px design border, but CTk scales border widths too, and below
        # one real pixel it draws nothing - so ask for enough to land on 1+.
        toggle = ctk.CTkFrame(bar, corner_radius=8, border_width=math.ceil(1 / self._scale), height=34,
                              cursor="hand2")
        toggle.pack(side="left", padx=(12, 0))
        self._student_icon = self._icon_label(toggle, "monitor-arrow-up", 16, MAROON, height=20)
        self._student_icon.pack(side="left", padx=(12, 8), pady=6)
        self._student_label = self._label(toggle, "Open student display", (FONT, 14, "bold"), MAROON, height=20)
        self._student_label.pack(side="left", padx=(0, 12), pady=6)
        for widget in (toggle, self._student_icon, self._student_label):
            widget.bind("<Button-1>", lambda _e: self._toggle_student_display())
            widget.configure(cursor="hand2")
        self._student_toggle = toggle
        self._refresh_student_toggle()

    def _refresh_student_toggle(self):
        is_open = self._student is not None
        bg, fg = (INK, SURFACE) if is_open else (SURFACE, MAROON)
        self._student_toggle.configure(fg_color=bg, border_color=INK if is_open else MAROON)
        self._student_icon.configure(text=_icon("monitor" if is_open else "monitor-arrow-up"), text_color=fg,
                                     fg_color=bg)
        self._student_label.configure(
            text="Student display open · Close" if is_open else "Open student display", text_color=fg, fg_color=bg,
        )

    def _toggle_student_display(self):
        if self._student is not None:
            self._student.close()  # its on_close clears self._student
            return
        # Imported here, not at the top: student_display imports this module.
        from student_display import StudentDisplayWindow

        self._student = StudentDisplayWindow(
            self.window, self.gate_location, self.direction, self._get_preview_frame,
            is_camera_down=lambda: self._showing_no_camera,
            on_card_key=self._forward_card_key,
            on_close=self._student_closed,
            fps=self._student_display_fps,
        )
        self._student.update_recognitions(self._latest_recognitions, self._latest_image_size)
        self._refresh_student_toggle()

    def _student_closed(self):
        self._student = None
        if not self._closed:
            self._refresh_student_toggle()

    def _forward_card_key(self, event):
        """A card tap typed into the student display (it had focus) goes to
        the same hidden field the reader normally types into."""
        if event.keysym in ("Return", "KP_Enter"):
            self._handle_card_input(event)
        elif event.char and event.char.isprintable():
            self._card_input_var.set(self._card_input_var.get() + event.char)
        return "break"

    # ---- public API: camera side ------------------------------------------

    def show_recognitions(self, recognitions, image_size):
        """recognitions: list of dicts with box/matched/name/student_id/
        department/confidence/log_id/deduped, one per face in the latest
        scanned frame. image_size: (width, height) of that frame, so boxes
        (in that frame's pixel coordinates) can be scaled onto the preview."""
        self._queue.put(("recognitions", (recognitions, image_size)))

    def show_offline(self, offline):
        self._queue.put(("offline", offline))

    def seed_stats(self, entries_today, unknown_today, spoof_today=0, occlusion_today=0):
        self._queue.put(("seed", (entries_today, unknown_today, spoof_today, occlusion_today)))

    def set_threshold(self, threshold):
        self._queue.put(("threshold", threshold))

    def set_alert_settings(self, alerts):
        """alerts: {"spoof": bool} from the backend (Settings page) - whether
        a suspected fake gets the alarm banner and sound."""
        self._queue.put(("alert_settings", alerts))

    # ---- public API: card side --------------------------------------------

    def show_card_status(self, message):
        self._queue.put(("card_status", message))

    def show_card_match(self, profile):
        self._queue.put(("card_match", profile))

    def show_card_failure(self, reason, reason_code=None):
        self._queue.put(("card_failure", (reason, reason_code)))

    def set_queue_count(self, count):
        self._queue.put(("queue_count", count))

    # ---- public API: guard sign-in ------------------------------------------

    def set_gate_sign_in(self, status):
        """status: {"enabled": bool, "on_duty": None or {"shift_id", "name",
        ...}} from the backend - shown in the status bar."""
        self._queue.put(("gate_sign_in", status))

    def show_sign_in_result(self, ok, message):
        """The answer to a password sign-in from the sign-in window."""
        self._queue.put(("sign_in_result", (ok, message)))

    def show_staff_signed_in(self, name):
        """A guard's staff ID card tap signed them in."""
        self._queue.put(("staff_signed_in", name))

    def current_shift_id(self):
        """The shift on duty now (for signing out when the window closes) -
        read from the Tk thread's last status, so it's safe from any thread."""
        on_duty = self._sign_in.get("on_duty") or {}
        return on_duty.get("shift_id")

    def set_backend_ok(self, ok):
        self._queue.put(("backend_ok", ok))

    def set_camera_ok(self, ok):
        self._queue.put(("camera_ok", ok))

    def is_closed(self):
        """True once the window is gone - lets main.py's status poll stop
        rescheduling itself instead of raising against a destroyed widget."""
        return self._closed or not self.window.winfo_exists()

    def run(self):
        """Hands control to Tk. The monitor owns the root window, so this is
        the entry-agent's main loop - it returns when the window closes."""
        self.window.mainloop()

    # ---- internals ----------------------------------------------------

    def _process_queue(self):
        if self._closed:
            return
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                try:
                    self._handle_queue_item(kind, payload)
                except Exception:
                    # One bad update is logged and dropped. Letting it escape
                    # used to skip the reschedule below, and the whole window
                    # then stopped updating for the rest of the shift.
                    traceback.print_exc()
        except queue.Empty:
            pass
        self.window.after(100, self._process_queue)

    def _handle_queue_item(self, kind, payload):
        if kind == "recognitions":
            recognitions, image_size = payload
            self._render_recognitions(recognitions, image_size)
        elif kind == "offline":
            self._render_offline(payload)
        elif kind == "seed":
            entries, unknown, spoof, occlusion = payload
            self.stats["entries"] = entries
            self.stats["unknown"], self.stats["spoof"] = unknown, spoof
            self.stats["occlusion"] = occlusion
            self._refresh_stat_labels()
        elif kind == "threshold":
            self.threshold_label.configure(text=f"{payload:.2f}")
        elif kind == "alert_settings":
            self._alert_spoof = bool(payload.get("spoof", True))
        elif kind == "card_match":
            self._render_card_match(payload)
        elif kind == "card_failure":
            reason, reason_code = payload
            self._render_card_failure(reason, reason_code)
        elif kind == "card_status":
            self._show_card_waiting()
        elif kind == "queue_count":
            self.queue_label.configure(
                text=f"Queue {payload}", text_color=CAUTION if payload else INK_600
            )
        elif kind == "backend_ok":
            self._set_status(self.backend_icon, self.backend_label, payload,
                             "Backend OK", "Backend not OK", DANGER, "x-circle")
        elif kind == "camera_ok":
            self._set_status(self.camera_icon, self.camera_label, payload,
                             "Camera OK", "Camera not connected", CAUTION, "warning")
        elif kind == "gate_sign_in":
            self._render_gate_sign_in(payload)
        elif kind == "sign_in_result":
            ok, message = payload
            self._render_sign_in_result(ok, message)
        elif kind == "staff_signed_in":
            self._render_staff_signed_in(payload)

    def _set_status(self, icon_label, text_label, ok, ok_text, bad_text, bad_color, bad_icon):
        color = VERIFIED if ok else bad_color
        icon_label.configure(text=_icon("check-circle" if ok else bad_icon), text_color=color)
        text_label.configure(text=ok_text if ok else bad_text, text_color=color)

    def _render_offline(self, offline):
        if offline:
            self.sync_icon.configure(text=_icon("clock-counter-clockwise"), text_color=CAUTION)
            self.sync_label.configure(text="Not synced", text_color=CAUTION)
        else:
            self.sync_icon.configure(text=_icon("check-circle"), text_color=VERIFIED)
            self.sync_label.configure(text="Synced", text_color=VERIFIED)

    # ---- guard sign-in --------------------------------------------------

    def _render_gate_sign_in(self, status):
        """Status bar: the typed-in guard name while sign-in is off; "On
        duty: <name> · Sign out" or an amber "No guard signed in · Sign in"
        while it's on. With nobody on duty - the gate monitor just opened, or
        a shift ended - the sign-in window opens by itself too: the post
        shouldn't go unattended just because nobody noticed the amber line.
        Scanning carries on either way."""
        self._sign_in = status or {"enabled": False, "on_duty": None}
        if not self._sign_in.get("enabled") or self._sign_in.get("on_duty"):
            self._cancel_sign_in_reminder()
        if not self._sign_in.get("enabled"):
            self._duty.pack_forget()
            if self._identity_label is not None and not self._identity_label.winfo_manager():
                self._identity_label.pack(side="left", padx=(26, 0))
            self._close_sign_in_dialog()
            return
        if self._identity_label is not None:
            self._identity_label.pack_forget()
        if not self._duty.winfo_manager():
            self._duty.pack(side="left", padx=(26, 0))
        on_duty = self._sign_in.get("on_duty")
        if on_duty:
            self._duty_icon.configure(text=_icon("user-focus"), text_color=VERIFIED)
            self._duty_label.configure(text=f"On duty: {on_duty.get('name') or 'Guard'}", text_color=INK)
            self._duty_action.configure(text="Sign out")
            self._close_sign_in_dialog()
        else:
            self._duty_icon.configure(text=_icon("warning"), text_color=CAUTION)
            self._duty_label.configure(text="No guard signed in", text_color=CAUTION)
            self._duty_action.configure(text="Sign in")
            # Not while a "Not now" reminder is pending - it asks again then.
            if self._sign_in_reminder is None:
                self._open_sign_in_dialog()

    def _cancel_sign_in_reminder(self):
        if self._sign_in_reminder is not None:
            self.window.after_cancel(self._sign_in_reminder)
            self._sign_in_reminder = None

    def _dismiss_sign_in_dialog(self):
        """Not now (or the window's X): put the sign-in window away, and ask
        again in SIGN_IN_REMIND_MS if the post is still empty."""
        self._close_sign_in_dialog()
        self._cancel_sign_in_reminder()
        self._sign_in_reminder = self.window.after(SIGN_IN_REMIND_MS, self._remind_sign_in)

    def _remind_sign_in(self):
        self._sign_in_reminder = None
        if not self._closed and self._sign_in.get("enabled") and not self._sign_in.get("on_duty"):
            self._open_sign_in_dialog()

    def _duty_clicked(self):
        on_duty = self._sign_in.get("on_duty")
        if not on_duty:
            self._open_sign_in_dialog()
            return
        name = on_duty.get("name") or "this guard"
        if messagebox.askyesno(
            "Sign out", f"Sign {name} out of {self.gate_location}?\n\nEntries after this are marked Unattended "
                        "until the next guard signs in.", parent=self.window,
        ) and self._on_sign_out:
            self._on_sign_out(on_duty.get("shift_id"))

    def _open_sign_in_dialog(self):
        if self._sign_in_dialog is not None:
            self._sign_in_dialog.lift()
            return
        dialog = ctk.CTkToplevel(self.window)
        dialog.title(f"Sign in for duty - {self.gate_location}")
        dialog.configure(fg_color=SURFACE)
        dialog.resizable(False, False)
        dialog.transient(self.window)
        dialog.protocol("WM_DELETE_WINDOW", self._dismiss_sign_in_dialog)
        _apply_icon(dialog)

        body = ctk.CTkFrame(dialog, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=28, pady=24)
        self._label(body, "Sign in for duty", (SEMI_HEAVY, 22), INK, anchor="w").pack(fill="x")
        self._label(body, f"Nobody is on duty at {self.gate_location}. Tap your staff ID card on the reader, or "
                          "type your username and password.",
                    (FONT, 14), INK_600, anchor="w", justify="left", wraplength=380).pack(fill="x", pady=(6, 4))
        self._label(body, "The gate keeps scanning meanwhile - its entries are marked Unattended until a guard "
                          "signs in.",
                    (FONT, 13), INK_600, anchor="w", justify="left", wraplength=380).pack(fill="x", pady=(0, 16))
        self._sign_in_fields = {}
        for key, text, secret in (("username", "Username", False), ("password", "Password", True)):
            self._label(body, text, (FONT, 14, "bold"), INK, anchor="w").pack(fill="x")
            # A light fill and a border of at least one real pixel: this
            # window is scaled with the monitor, and CTk draws nothing for a
            # border scaled below one pixel (same fix as the student toggle).
            entry = ctk.CTkEntry(body, width=380, height=40, corner_radius=3,
                                 border_width=math.ceil(1 / self._scale), border_color=INK_400,
                                 fg_color=CANVAS, text_color=INK, font=(FONT, 14), show="•" if secret else "")
            entry.pack(fill="x", pady=(4, 12))
            burst = _KeyBurst(entry)
            entry.bind("<Return>", lambda _e, e=entry, b=burst: self._sign_in_enter(e, b))
            self._sign_in_fields[key] = entry
        self._sign_in_error = self._label(body, "", (FONT, 13, "bold"), DANGER, anchor="w", justify="left",
                                          wraplength=380)
        self._sign_in_error.pack(fill="x")
        buttons = ctk.CTkFrame(body, fg_color="transparent")
        buttons.pack(fill="x", pady=(14, 0))
        self._sign_in_submit = ctk.CTkButton(
            buttons, text="Sign in", font=(FONT, 14, "bold"), height=42, corner_radius=8, fg_color=MAROON,
            hover_color=MAROON_DEEP, text_color="white", command=self._submit_sign_in,
        )
        self._sign_in_submit.pack(side="right")
        ctk.CTkButton(
            buttons, text="Not now", font=(FONT, 14, "bold"), height=42, corner_radius=8, fg_color=SURFACE,
            hover_color=CANVAS, text_color=INK, border_width=math.ceil(1 / self._scale), border_color=INK_400,
            command=self._dismiss_sign_in_dialog,
        ).pack(side="right", padx=(0, 10))

        self._sign_in_dialog = dialog
        # Centered by the size it asks for - its drawn size isn't known yet.
        dialog.update_idletasks()
        x = self.window.winfo_rootx() + (self.window.winfo_width() - dialog.winfo_reqwidth()) // 2
        y = self.window.winfo_rooty() + (self.window.winfo_height() - dialog.winfo_reqheight()) // 3
        dialog.geometry(f"+{max(0, x)}+{max(0, y)}")
        dialog.after(150, lambda: (dialog.lift(), self._sign_in_fields["username"].focus_force()))

    def _sign_in_enter(self, entry, burst):
        """Enter in the sign-in window: a card tap (the reader typed into the
        focused box) signs in by card; otherwise username -> password ->
        submit."""
        card = burst.take_card()
        if card:
            self._last_card_id = card
            if self.on_tap:
                self.on_tap(card)
            return "break"
        if entry is self._sign_in_fields["username"]:
            self._sign_in_fields["password"].focus_set()
        else:
            self._submit_sign_in()
        return "break"

    def _submit_sign_in(self):
        username = self._sign_in_fields["username"].get().strip()
        password = self._sign_in_fields["password"].get()
        if not username or not password:
            self._sign_in_error.configure(text="Type your username and password - or tap your staff ID card.")
            return
        self._sign_in_error.configure(text="")
        self._sign_in_submit.configure(state="disabled", text="Signing in…")
        if self._on_sign_in:
            self._on_sign_in(username, password)

    def _render_sign_in_result(self, ok, message):
        if self._sign_in_dialog is None:
            return
        if ok:
            self._close_sign_in_dialog()
            return
        self._sign_in_submit.configure(state="normal", text="Sign in")
        self._sign_in_fields["password"].delete(0, "end")
        self._sign_in_error.configure(text=message or "Couldn't sign in.")

    def _close_sign_in_dialog(self):
        if self._sign_in_dialog is not None:
            self._sign_in_dialog.destroy()
            self._sign_in_dialog = None

    def _render_staff_signed_in(self, name):
        """Card strip after a staff ID card tap signed its owner in."""
        timestamp = datetime.now().strftime("%I:%M:%S %p")
        tile = self._s["tile"]
        self._set_card_body_color(SURFACE, VERIFIED)
        self._set_card_tile(_glyph_tile(self._px(tile), VERIFIED, "user-focus", SURFACE, scale=0.56, bold=True),
                            tile, 8)
        self.card_title.configure(text=self._fit_card_text(name, (FONT, 28, "bold")), text_color=INK,
                                  font=(FONT, 28, "bold"))
        self.card_sub.configure(text=f"Signed in for duty at {self.gate_location}")
        self._pack_card_lines(self.card_title, self.card_sub)
        self.card_word_icon.configure(text=_icon("check-circle"), text_color=VERIFIED)
        self.card_word.configure(text="ON DUTY", text_color=VERIFIED)
        self.card_time.configure(text=timestamp)
        self.card_time.pack(anchor="e")
        self.card_right.grid()
        self._schedule_card_reset()
        self._close_sign_in_dialog()

    # ---- card scanner ---------------------------------------------------

    def _handle_card_input(self, _event):
        nfc_id = self._card_input_var.get().strip()
        self._card_input_var.set("")
        if not nfc_id:
            self.show_card_failure("Card read failed - try again.", "read_error")
            return
        self._last_card_id = nfc_id
        if self.on_tap:
            self.on_tap(nfc_id)

    def _keep_focus(self):
        if self._closed:
            return
        # The reader is HID-keyboard-emulation, so this hidden field has to
        # stay focused for a tap to land anywhere at all. Nothing else in
        # this window takes typed input, so reclaiming focus can't interrupt
        # the guard - and mouse-wheel scrolling of the live log is bound to
        # the pointer, not focus, so the log still scrolls freely. The one
        # exception is the student display: pulling focus back from it would
        # also pull the gate monitor over it, and it forwards taps itself.
        try:
            focused = self.window.focus_get()
        except (KeyError, tk.TclError):
            focused = None
        student_has_focus = self._student is not None and self._student.owns(focused)
        # The sign-in window takes typing too - and catches a card tap
        # itself (see _KeyBurst).
        dialog_has_focus = (self._sign_in_dialog is not None and focused is not None
                            and str(focused).startswith(str(self._sign_in_dialog)))
        if focused is not self._card_input and not student_has_focus and not dialog_has_focus:
            self._card_input.focus_force()
        self.window.after(FOCUS_CHECK_MS, self._keep_focus)

    def _set_card_body_color(self, color, bar_color):
        self.card_panel.configure(fg_color=bar_color)
        self.card_body.configure(fg_color=color)
        for frame in (self.card_text, self.card_right, self.card_word_row):
            frame.configure(bg_color=color)
        for label in (self.card_tile, self.card_title, self.card_sub, self.card_ids, self.card_note,
                      self.card_word_icon, self.card_word, self.card_time):
            label.configure(fg_color=color)

    def _set_card_tile(self, pil_image, size, radius):
        """pil_image is already at real pixel size; size is logical."""
        image = self._ctk_image(_round_corners(pil_image, self._px(radius)), size)
        self._card_images["tile"] = image  # keep a reference or Tk drops it
        self.card_tile.configure(image=image, width=size, height=size)

    def _pack_card_lines(self, *labels):
        """Shows exactly these text lines, in this order - re-packing from
        scratch each time so a line hidden by the previous result can't end
        up in the wrong place."""
        for label in (self.card_title, self.card_sub, self.card_ids, self.card_note):
            label.pack_forget()
        for label in labels:
            label.pack(fill="x", anchor="w")

    def _show_card_waiting(self):
        tile = self._s["tile"]
        self._set_card_body_color(SURFACE, LINE)
        self._set_card_tile(_glyph_tile(self._px(tile), CANVAS, "identification-card", INK_600, scale=0.56), tile, 8)
        self.card_title.configure(text="Tap a card", text_color=INK, font=(SEMI_HEAVY, self._s["tap_title"]))
        self.card_sub.configure(text="Hold ID near the reader")
        self._pack_card_lines(self.card_title, self.card_sub)
        self.card_right.grid_remove()

    def _render_card_match(self, profile):
        timestamp = datetime.now().strftime("%I:%M:%S %p")
        name = profile.get("name") or "Unknown"
        direction = (profile.get("direction") or self.direction or "entry").upper()

        self._set_card_body_color(SURFACE, VERIFIED)
        photo_size = self._s["photo"]
        photo = _avatar_image(profile.get("photo_bytes"), self._px(photo_size))
        if photo is None:
            photo = _initials_avatar(name, self._px(photo_size))
        self._set_card_tile(photo, photo_size, 3)
        self.card_title.configure(
            text=self._fit_card_text(name, (FONT, 28, "bold")), text_color=INK, font=(FONT, 28, "bold")
        )
        role = (profile.get("role") or "").capitalize()
        self.card_sub.configure(text=" · ".join(part for part in (role, profile.get("department")) if part) or "—")
        self.card_ids.configure(
            text=f"{profile.get('student_id') or '—'} · Card {profile.get('card_id') or '—'}", text_color=INK_600
        )
        note = profile.get("distinguishing_note")
        if note:
            self.card_note.configure(text=f"Note: {note}", text_color=CAUTION)
            self._pack_card_lines(self.card_title, self.card_sub, self.card_ids, self.card_note)
        else:
            self._pack_card_lines(self.card_title, self.card_sub, self.card_ids)
        self.card_word_icon.configure(text=_icon("identification-card"), text_color=VERIFIED)
        self.card_word.configure(text=f"CARD · {direction}", text_color=VERIFIED)
        self.card_time.configure(text=timestamp)
        self.card_time.pack(anchor="e")
        self.card_right.grid()
        self._schedule_card_reset()
        if self._student is not None:
            self._student.show_card_result("accepted")

        self._push_log_row(
            kind="card", name=name, id_text=profile.get("student_id") or "—", time=timestamp,
            word=f"CARD · {direction}", photo_bytes=profile.get("photo_bytes"), initials_seed=name,
        )

    def _render_card_failure(self, reason, reason_code):
        timestamp = datetime.now().strftime("%I:%M:%S %p")
        card_id = self._last_card_id
        card_text = f"Card {card_id} · {timestamp}" if card_id else timestamp
        tile = self._s["tile"]

        if reason_code == "offline":
            # Not a rejection - the backend just couldn't be reached, and the
            # tap is already sitting in the offline queue.
            self._set_card_body_color(CAUTION_TINT, CAUTION)
            self._set_card_tile(
                _glyph_tile(self._px(tile), CAUTION, "cloud-slash", SURFACE, scale=0.56, bold=True), tile, 8
            )
            self.card_title.configure(
                text="Offline — tap queued, will sync automatically", text_color=INK, font=(FONT, 20, "bold")
            )
            self.card_ids.configure(text=card_text, text_color=INK)
            self._pack_card_lines(self.card_title, self.card_ids)
            self.card_right.grid_remove()
            kind, label = "card_offline", "Tap queued"
        else:
            label = self.FAILURE_HEADLINES.get(reason_code, "Access denied")
            self._set_card_body_color(DANGER_TINT, DANGER)
            self._set_card_tile(
                _glyph_tile(self._px(tile), DANGER, "identification-card", SURFACE, scale=0.56, bold=True), tile, 8
            )
            self.card_title.configure(text=label, text_color=DANGER, font=(SEMI_HEAVY, self._s["tap_title"]))
            self.card_ids.configure(text=card_text, text_color=INK)
            # The backend's own reason, when it says more than the headline
            # (e.g. "Rejected: check SERVICE_TOKEN ...").
            if reason and reason.rstrip(".") != label:
                self.card_note.configure(text=reason, text_color=INK_600)
                self._pack_card_lines(self.card_title, self.card_ids, self.card_note)
            else:
                self._pack_card_lines(self.card_title, self.card_ids)
            self.card_word_icon.configure(text=_icon("x-circle"), text_color=DANGER)
            self.card_word.configure(text="CARD REJECTED", text_color=DANGER)
            self.card_time.pack_forget()
            self.card_right.grid()
            kind = "card_rejected"
        self._schedule_card_reset()
        if self._student is not None and not (reason_code or "").startswith("staff_"):
            # A guard's own staff card is none of the student display's
            # business. Only a card the system actually looked up and refused reads as
            # "not registered" - a misread or a server hiccup asks for a retap.
            outcome = {"offline": "queued", "not_registered": "rejected",
                       "deactivated": "deactivated"}.get(reason_code, "error")
            self._student.show_card_result(outcome)

        self._push_log_row(
            kind=kind, name=label if kind == "card_rejected" else "Card tap queued",
            id_text=f"Card {card_id}" if card_id else "Card", time=timestamp,
            word="CARD REJECTED" if kind == "card_rejected" else "QUEUED",
            feature_word=f"CARD REJECTED · {label}" if kind == "card_rejected" else "OFFLINE · TAP QUEUED",
        )

    def _fit_card_text(self, text, font):
        """Ellipsizes a long name so it can't push the card strip's right-
        hand block (CARD · ENTRY + time) off the panel."""
        available = self.card_body.winfo_width() - self._px(self._s["photo"] + 40 + 220)
        family, size = font[0], font[1]
        weight = font[2] if len(font) > 2 else "normal"
        return _ellipsize(self._tkfont(family, size, weight), text, available)

    def _schedule_card_reset(self):
        if self._card_reset_job:
            self.window.after_cancel(self._card_reset_job)
        self._card_reset_job = self.window.after(RESET_DELAY_MS, self._reset_card)

    def _reset_card(self):
        self._card_reset_job = None
        if self._closed:
            return
        self._show_card_waiting()

    # ---- camera events ----------------------------------------------------

    def _render_recognitions(self, recognitions, image_size):
        self._latest_recognitions = recognitions
        self._latest_image_size = image_size
        self.stat_tiles["in_frame"].configure(text=str(len(recognitions)))

        for item in recognitions:
            log_id = item.get("log_id")
            is_new_event = bool(log_id) and not item.get("deduped") and log_id not in self._seen_log_ids
            if not is_new_event:
                continue
            self._seen_log_ids.add(log_id)
            if item.get("spoof_suspected"):
                self.stats["spoof"] += 1
                # Still counted and logged either way - the Settings page's
                # "Alert on suspected fake face" only silences the alarm.
                if self._alert_spoof:
                    self._show_alert_banner("spoof")
                    _play_alert_sound()
            elif item.get("occlusion_suspected"):
                # Only reached by an older backend - covered faces no longer
                # come with a log_id (see IdentifyView._occlusion_prompt), so
                # the face's own "PLEASE UNCOVER YOUR FACE" label is all they get.
                self.stats["occlusion"] += 1
            elif item["matched"]:
                self.stats["entries"] += 1
            else:
                self.stats["unknown"] += 1
                if item.get("repeated_unknown"):
                    count = item.get("repeated_unknown_count")
                    sub = f"Seen {count} times at this gate recently — check this person" if count else None
                    self._show_alert_banner("repeated", sub)
                else:
                    self._show_alert_banner("unknown")
                _play_alert_sound()
            self._push_log_entry(item)

        self._refresh_stat_labels()
        if self._student is not None:
            self._student.update_recognitions(recognitions, image_size)

    def _show_alert_banner(self, kind, sub=None):
        """Docks the alarm banner across the top of the video panel - fires
        once per genuinely new unknown-face or spoof event (the same log_id
        dedup the stats/log rely on), not on every ~0.2s poll while the
        person is still in frame. Just sets state here - _draw_overlays,
        called on every video refresh tick, is what draws it."""
        self._alert = {"kind": kind, "time": datetime.now().strftime("%I:%M:%S %p"), "sub": sub}
        if self._alert_hide_job:
            self.window.after_cancel(self._alert_hide_job)
        self._alert_hide_job = self.window.after(self.ALERT_DISPLAY_MS, self._hide_alert_banner)

    def _hide_alert_banner(self):
        self._alert_hide_job = None
        self._alert = None
        # No explicit redraw needed - _update_video's own timer picks this up
        # and simply stops drawing the banner on its next tick.

    def _refresh_stat_labels(self):
        self.stat_tiles["entries"].configure(text=str(self.stats["entries"]))
        self.stat_tiles["unknown"].configure(text=str(self.stats["unknown"]))
        self.stat_tiles["spoof"].configure(text=str(self.stats["spoof"]))
        self.stat_tiles["today"].configure(text=str(self.stats["entries"]))

    # ---- live log ---------------------------------------------------------

    def _push_log_row(self, *, kind, name, id_text, time, word=None, feature_word=None,
                      conf=None, photo_bytes=None, initials_seed=None, extra=""):
        """The one way anything reaches the live log. Both credentials go
        through here - a face event from the scan loop and an NFC tap render
        as the same kind of row, differing only in their kind - so the log
        is a single chronological record of the gate rather than two
        half-stories."""
        self._log_entries.insert(0, {
            "kind": kind,
            "name": name,
            "id_text": id_text,
            "time": time,
            "word": word or LOG_KINDS[kind]["word"],
            "feature_word": feature_word or word or LOG_KINDS[kind]["word"],
            "conf": conf,
            "photo_bytes": photo_bytes,
            "initials_seed": initials_seed,
            "extra": extra,
        })
        self._log_entries = self._log_entries[: self.MAX_LOG_ROWS]
        self._rebuild_log_list()

    def _push_log_entry(self, item):
        timestamp = datetime.now().strftime("%I:%M:%S %p")
        # A moment of occlusion seen earlier in this same encounter, even
        # though it isn't this event's own outcome - appended to the meta
        # line rather than dropped, per EntryLog.occlusion_detected.
        extra = " · face briefly covered" if item.get("occlusion_seen") else ""
        if item.get("spoof_suspected"):
            self._push_log_row(
                kind="spoof", name="Possible spoof", id_text="Liveness check failed", time=timestamp,
                feature_word="SPOOF SUSPECTED", photo_bytes=item.get("photo_bytes"), extra=extra,
            )
        elif item.get("occlusion_suspected"):
            self._push_log_row(
                kind="covered", name="Face covered", id_text="Mouth/nose covered", time=timestamp,
                feature_word="PLEASE UNCOVER YOUR FACE", photo_bytes=item.get("photo_bytes"),
            )
        elif item["matched"]:
            self._push_log_row(
                kind="entry", name=item["name"], id_text=item.get("student_id") or "—", time=timestamp,
                conf=item.get("confidence"), photo_bytes=item.get("photo_bytes"),
                initials_seed=item["name"], extra=extra,
            )
        else:
            self._push_log_row(
                kind="unknown", name="Unknown", id_text="No ID on file", time=timestamp,
                photo_bytes=item.get("photo_bytes"), extra=extra,
            )

    def _meta(self, entry):
        conf = f" · {entry['conf']}%" if entry.get("conf") is not None else ""
        return f"{entry['id_text']} · {entry['time']}{conf}{entry['extra']}"

    def _render_featured(self, entry):
        s = self._s
        view = LOG_KINDS[entry["kind"]]
        color = view["color"]
        self.feat_band.configure(fg_color=color)
        self.feat_band_row.configure(fg_color=color)
        for widget in (self.feat_icon, self.feat_word, self.feat_time):
            widget.configure(fg_color=color)
        self.feat_icon.configure(text=_icon(view["icon"]))
        self.feat_word.configure(text=entry["feature_word"])
        self.feat_time.configure(text=entry["time"])

        image = self._thumb(entry, s["feat_thumb"])
        self._log_photo_images.append(image)
        self.feat_thumb.configure(image=image)
        body_width = self.feat_body.winfo_width() / self._scale
        # Before the window is first laid out the width reads as ~1 - fall
        # back to a sensible wrap rather than wrapping every word.
        reserved = s["feat_thumb"] + 60 + (110 if entry.get("conf") is not None else 0)
        wrap = max(140, body_width - reserved if body_width > 200 else 360)
        # The ID line under the name must always stay visible. A wide card
        # allows two lines (ellipsized well short of two full widths, since
        # word wrap never packs lines completely); a narrow one gets a
        # single ellipsized line, where word wrap would otherwise spill a
        # long name onto a third.
        name_font = self._tkfont(FONT, s["feat_name"], "bold")
        if wrap >= 300:
            name = _ellipsize(name_font, entry["name"], self._px(wrap) * 1.6)
        else:
            name = _ellipsize(name_font, entry["name"], self._px(wrap))
        self.feat_name.configure(text=name, wraplength=wrap)
        self.feat_id.configure(text=entry["id_text"] + entry["extra"])
        if entry.get("conf") is not None:
            self.feat_conf.configure(text=f"{entry['conf']}%")
            self.feat_match.grid()
        else:
            self.feat_match.grid_remove()

    def _rebuild_log_list(self):
        for child in self.log_list.winfo_children():
            child.destroy()
        self._log_photo_images = []  # keep CTkImage refs alive - Tk drops unreferenced ones

        if not self._log_entries:
            self.feat_card.grid_remove()
            self.feat_empty.grid(row=1, column=0, sticky="w", pady=(4, 0))
            self._label(self.log_list, "Earlier events appear here.", (FONT, 14), INK_400).grid(
                row=0, column=0, sticky="w", padx=14, pady=14
            )
            return

        self.feat_empty.grid_remove()
        self.feat_card.grid()
        self._render_featured(self._log_entries[0])

        s = self._s
        earlier = self._log_entries[1:]
        if not earlier:
            self._label(self.log_list, "Earlier events appear here.", (FONT, 14), INK_400).grid(
                row=0, column=0, sticky="w", padx=14, pady=14
            )
            return
        row_font = self._tkfont(FONT, s["row_name"], "bold")
        meta_font = self._tkfont(FONT_MONO_MEDIUM, 14)
        word_font = self._tkfont(COND_HEAVY, s["row_word"])
        panel_width = self._log_panel.winfo_width()
        if panel_width <= 1:
            panel_width = self._px(560)  # not laid out yet - _on_log_configure redoes this once it is
        for index, entry in enumerate(earlier):
            view = LOG_KINDS[entry["kind"]]
            bg = view.get("tint", SURFACE)
            row = ctk.CTkFrame(self.log_list, fg_color=bg, corner_radius=0)
            row.grid(row=index * 2, column=0, sticky="ew")
            row.grid_columnconfigure(2, weight=1)
            ctk.CTkFrame(row, fg_color=view["color"] if view.get("sec") else bg, width=3, height=1, corner_radius=0).grid(
                row=0, column=0, sticky="ns"
            )
            thumb = self._thumb(entry, self.LIST_THUMB_SIZE)
            self._log_photo_images.append(thumb)
            ctk.CTkLabel(row, image=thumb, text="", fg_color=bg).grid(row=0, column=1, padx=(14, 14), pady=10)

            text = ctk.CTkFrame(row, fg_color=bg, corner_radius=0)
            text.grid(row=0, column=2, sticky="ew")
            # The scrollable frame's own width is its content's width, not
            # the visible width - measure the panel instead, less the thumb
            # column, this row's status word, paddings and the scrollbar.
            word_w = word_font.measure(entry["word"]) + self._px(s["row_word"] + 6 + 10 + 16)
            name_width = panel_width - self._px(self.LIST_THUMB_SIZE + 28 + 40) - word_w
            self._label(
                text, _ellipsize(row_font, entry["name"], name_width), (FONT, s["row_name"], "bold"), INK,
                anchor="w", fg_color=bg,
            ).pack(fill="x", anchor="w")
            self._label(
                text, _ellipsize(meta_font, self._meta(entry), name_width), (FONT_MONO_MEDIUM, 14), INK_600,
                anchor="w", fg_color=bg,
            ).pack(fill="x", anchor="w")

            word = ctk.CTkFrame(row, fg_color=bg, corner_radius=0)
            word.grid(row=0, column=3, sticky="e", padx=(10, 16))
            self._icon_label(word, view["icon"], s["row_word"], view["color"], fg_color=bg).pack(
                side="left", padx=(0, 6)
            )
            self._label(word, entry["word"], (COND_HEAVY, s["row_word"]), view["color"], fg_color=bg).pack(side="left")

            # Plain tk.Frame - a CTkFrame this thin draws nothing at all.
            tk.Frame(self.log_list, bg=LINE, height=max(1, self._px(1))).grid(
                row=index * 2 + 1, column=0, sticky="ew"
            )

    # ---- video panel ------------------------------------------------------

    def _update_video(self):
        if self._closed:
            return
        try:
            self._refresh_video()
        except Exception:
            # Logged, then the next frame tries again - an escaped error here
            # used to skip the reschedule and leave the feed frozen for good.
            traceback.print_exc()
        self.window.after(self._video_refresh_ms, self._update_video)

    def _refresh_video(self):
        frame = self._get_preview_frame()
        if frame is not None:
            self._last_frame_at = time.monotonic()
            if self._showing_no_camera:
                self._showing_no_camera = False
            self._draw_frame(frame)
        elif time.monotonic() - self._last_frame_at >= self.CAMERA_GRACE_SECONDS:
            # No frame for a while now - either no camera ever connected, or
            # one did and then stopped (unplugged, driver crash - see
            # Camera._close_after_failure). Camera's own background loop
            # keeps retrying the connection for as long as the app runs, so
            # the moment a frame arrives again the branch above takes back
            # over - no restart needed.
            if not self._showing_no_camera:
                self._showing_no_camera = True
                self.stat_tiles["in_frame"].configure(text="—")
            self._draw_no_camera_placeholder()

    def _panel_size(self):
        return (
            self.video_canvas.winfo_width() or self.MIN_VIDEO_SIZE[0],
            self.video_canvas.winfo_height() or self.MIN_VIDEO_SIZE[1],
        )

    def _strip_height(self):
        return self._px(self._s["strip"])

    # How much of the camera frame may be trimmed off (per axis) so the feed
    # fills the panel edge to edge. Past this - a very tall or very wide
    # window - the whole frame is shown with bars instead, so nobody standing
    # near the edge of the picture can be cropped out of view.
    MAX_FEED_CROP = 0.35

    def _compose_panel(self, frame_image=None):
        """The whole video panel as one image, filling the canvas cell: dark
        body, the top strip (filled with the alarm color while a banner
        shows), and the live frame filling the area below it (cropped a
        little if its shape differs from the panel's - see MAX_FEED_CROP) -
        then ONE rounded mask over the lot. Returns (image, panel rect,
        frame placement): the rect is (x, y, w, h) on the canvas; the
        placement is where the full, uncropped frame would sit, for mapping
        face boxes, which _draw_box then clips to the visible feed."""
        canvas_w, canvas_h = self._panel_size()
        rect = (0, 0, canvas_w, canvas_h)
        strip_h = self._strip_height()
        feed_w, feed_h = canvas_w, max(1, canvas_h - strip_h)
        self._feed_bounds = (0, strip_h, feed_w, strip_h + feed_h)
        panel = Image.new("RGBA", (canvas_w, canvas_h), INK)
        if self._alert:
            ImageDraw.Draw(panel).rectangle((0, 0, canvas_w, strip_h), fill=ALERTS[self._alert["kind"]]["color"])
        placement = None
        if frame_image is not None:
            src_w, src_h = frame_image.size
            cover = max(feed_w / src_w, feed_h / src_h)
            crop = 1 - min(feed_w / (src_w * cover), feed_h / (src_h * cover))
            scale = cover if crop <= self.MAX_FEED_CROP else min(feed_w / src_w, feed_h / src_h)
            img_w, img_h = max(1, round(src_w * scale)), max(1, round(src_h * scale))
            origin_x, origin_y = (feed_w - img_w) // 2, strip_h + (feed_h - img_h) // 2
            # Bilinear, not Lanczos: on a moving video frame the difference
            # isn't visible, and it's several times cheaper every frame.
            resized = frame_image.resize((img_w, img_h), Image.BILINEAR)
            # Paste only the part inside the feed area, so a cropped frame
            # can't spill up into the strip.
            visible = resized.crop((
                max(0, -origin_x), max(0, strip_h - origin_y),
                min(img_w, feed_w - origin_x), min(img_h, strip_h + feed_h - origin_y),
            ))
            panel.paste(visible, (max(0, origin_x), max(strip_h, origin_y)))
            placement = (origin_x, origin_y, img_w, img_h)
        panel.putalpha(self._corner_mask(panel.size, self._px(self.VIDEO_PANEL_CORNER_RADIUS)))
        return panel, rect, placement

    def _corner_mask(self, size, radius):
        """The rounded-corner mask for the video panel - built once per panel
        size and reused every frame (it used to be redrawn from scratch about
        24 times a second)."""
        key = (size, radius)
        if getattr(self, "_corner_mask_key", None) != key:
            mask = Image.new("L", size, 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
            self._corner_mask_key, self._corner_mask_image = key, mask
        return self._corner_mask_image

    def _show_panel(self, panel, rect):
        # Kept as self._video_image (not a local) - Tk drops a PhotoImage
        # with no surviving reference, which would blank the canvas.
        self._video_image = ImageTk.PhotoImage(panel)
        self.video_canvas.delete("all")
        self.video_canvas.create_image(rect[0], rect[1], image=self._video_image, anchor="nw")
        self._panel_bounds = rect

    def _canvas_text(self, x, y, text, family, px, color, anchor="w", weight="normal", width=None):
        kwargs = {"width": width} if width else {}
        return self.video_canvas.create_text(
            x, y, text=text, font=self._tkfont(family, px, weight), fill=color, anchor=anchor, **kwargs
        )

    def _draw_overlays(self, rect):
        """The top strip's contents - either the LIVE row (a square status
        dot, LIVE / NO SIGNAL, and the caption) or the alarm banner's icon,
        title, subtitle, speaker mark and time - drawn straight onto the
        canvas over the composited panel. Called last from both _draw_frame
        and _draw_no_camera_placeholder, which between them run continuously
        at VIDEO_REFRESH_MS, so the banner's appear/disappear timing needs no
        redraw call of its own."""
        s = self._s
        panel_x, panel_y, panel_w, _panel_h = rect
        strip_h = self._strip_height()
        cy = panel_y + strip_h / 2
        x = panel_x + self._px(26)
        if self._alert:
            alert = ALERTS[self._alert["kind"]]
            glyph = _icon(alert["icon"])
            if glyph:
                self._canvas_text(x, cy, glyph, ICON_FONT_BOLD, 36 * s["strip"] / 64, "white")
                x += self._px(36 * s["strip"] / 64 + 16)
            title_id = self._canvas_text(x, cy, alert["title"], SEMI_HEAVY, s["banner_title"], "white")
            x = self.video_canvas.bbox(title_id)[2] + self._px(16)
            # Right side first, so the subtitle can be ellipsized into
            # whatever width is left between the title and it.
            right_x = panel_x + panel_w - self._px(26)
            time_id = self._canvas_text(right_x, cy, self._alert["time"], FONT_MONO_MEDIUM, 16, "white", anchor="e")
            right_x = self.video_canvas.bbox(time_id)[0] - self._px(12)
            speaker = _icon("speaker-high")
            if speaker:
                speaker_id = self._canvas_text(right_x, cy, speaker, ICON_FONT_BOLD, 28 * s["strip"] / 64, "white", anchor="e")
                right_x = self.video_canvas.bbox(speaker_id)[0] - self._px(16)
            sub_font = self._tkfont(FONT, s["banner_sub"])
            sub = _ellipsize(sub_font, self._alert.get("sub") or alert["sub"], right_x - x)
            if sub and right_x - x > self._px(40):
                self._canvas_text(x, cy, sub, FONT, s["banner_sub"], "white")
            return

        live = not self._showing_no_camera
        dot = self._px(10)
        self.video_canvas.create_rectangle(
            x, cy - dot / 2, x + dot, cy + dot / 2, fill=BRASS if live else INK_400, outline=""
        )
        word_id = self._canvas_text(x + dot + self._px(10), cy, "LIVE" if live else "NO SIGNAL", COND_HEAVY, 16, "white")
        caption_x = self.video_canvas.bbox(word_id)[2] + self._px(16)
        self._canvas_text(caption_x, cy, self._caption_text, FONT_MONO, 14, INK_400)

    def _draw_no_camera_placeholder(self):
        s = self._s
        panel, rect, _placement = self._compose_panel(None)
        self._show_panel(panel, rect)
        panel_x, panel_y, panel_w, panel_h = rect
        strip_h = self._strip_height()
        cx, cy = panel_x + panel_w / 2, panel_y + strip_h + (panel_h - strip_h) / 2
        t = (s["strip"] - 52) / 12  # 0 at the narrow profile, 1 at full HD
        glyph = _icon("video-camera-slash", bold=False)
        if glyph:
            self._canvas_text(cx, cy - self._px(48), glyph, ICON_FONT, 44 + 12 * t, BRASS, anchor="s")
        wrap = min(panel_w - self._px(52), self._px(620))
        self._canvas_text(
            cx, cy - self._px(28), "No camera connected — card taps still work.", SEMI_HEAVY, 22 + 6 * t,
            "white", anchor="n", width=wrap,
        )
        self._canvas_text(
            cx, cy + self._px(28 + 14 * t), "Connecting a camera resumes automatically.", FONT, 16 + 4 * t,
            LINE, anchor="n", width=wrap,
        )
        index = self._initial_camera_index if self._initial_camera_index is not None else 0
        self._caption_text = f"Camera {index} · waiting for device"
        self._draw_overlays(rect)

    def _draw_frame(self, frame):
        rgb = frame[:, :, ::-1]  # BGR (OpenCV) -> RGB
        image = Image.fromarray(rgb)
        panel, rect, placement = self._compose_panel(image)
        self._show_panel(panel, rect)

        src_w, src_h = self._latest_image_size
        if src_w > 0 and src_h > 0 and placement:
            origin_x, origin_y, img_w, img_h = placement
            for item in self._latest_recognitions:
                self._draw_box(item, origin_x, origin_y, img_w, img_h, src_w, src_h)

        count = len(self._latest_recognitions)
        self._caption_text = f"{src_w}×{src_h} · {count} face{'s' if count != 1 else ''} tracked"
        self._draw_overlays(rect)

    @staticmethod
    def _box_style(item):
        """(color, icon, label) for one face on the feed."""
        if item.get("spoof_suspected"):
            # Shown red as soon as THIS frame's liveness score misses - a
            # guard should see the warning the moment it's suspected. While
            # item["retry"] is also true it's still just a suspicion, not yet
            # logged or alarmed, and the label says so.
            return DANGER, "warning-octagon", "SPOOF" if not item.get("retry") else "CHECKING · POSSIBLE SPOOF"
        if item.get("occlusion_suspected"):
            # Checked before the generic retry case: it needs its own color
            # even while unconfirmed. Telling someone to uncover their face
            # is harmless even if this frame's read turns out wrong, so the
            # label never hedges.
            return PROMPT, "hand-palm", "PLEASE UNCOVER YOUR FACE"
        if item.get("retry"):
            # Not a decided outcome yet - a skipped frame (blurry, edge-
            # cropped, turned away) or an unmatched face still short of
            # enough agreement to count as a real "Unknown". Neutral, so it
            # never reads as a red flag on a single bad frame.
            return INK_600, "circle-notch", item.get("hint") or "Checking…"
        if item.get("tiebreak"):
            # A confusable-pair tiebreak means the match itself looked fine -
            # it's overridden because this person is on record as easily
            # confused with someone similar (see users.models.ConfusablePair).
            label = "TAP CARD · LOOKALIKE CHECK" if item.get("confusable_pair") else "TAP CARD TO CONFIRM"
            return PROMPT, "identification-card", label
        if item["matched"]:
            label = item["name"]
            if item.get("confidence") is not None:
                label = f"{label} · {item['confidence']}%"
            return VERIFIED, "sign-in", label
        return CAUTION, "user-circle-dashed", "UNKNOWN"

    def _draw_box(self, item, origin_x, origin_y, img_w, img_h, src_w, src_h):
        box = item.get("box")
        if not box:
            return
        x0 = origin_x + (box["left"] / src_w) * img_w
        y0 = origin_y + (box["top"] / src_h) * img_h
        x1 = origin_x + (box["right"] / src_w) * img_w
        y1 = origin_y + (box["bottom"] / src_h) * img_h
        # Clipped to the visible feed - the frame may be cropped a little to
        # fill the panel (see _compose_panel), and a face in the trimmed
        # margin shouldn't draw its box over the strip or off the panel.
        feed_x0, feed_y0, feed_x1, feed_y1 = self._feed_bounds
        x0, y0 = max(x0, feed_x0), max(y0, feed_y0)
        x1, y1 = min(x1, feed_x1 - 1), min(y1, feed_y1 - 1)
        if x1 - x0 < self._px(8) or y1 - y0 < self._px(8):
            return  # face is (almost) entirely in the cropped-off margin
        color, icon_name, label = self._box_style(item)

        self.video_canvas.create_rectangle(x0, y0, x1, y1, outline=color, width=self._px(2))

        # The name tab: a solid color tab sitting on the box's top edge,
        # icon + label in white. Flipped inside the box if it would run up
        # into the top strip.
        tab_px = self._s["tab"]
        text_font = self._tkfont(FONT, tab_px, "bold")
        glyph = _icon(icon_name)
        icon_w = self._px(tab_px + 6) if glyph else 0
        pad_x, pad_y = self._px(10), self._px(6)
        tab_h = text_font.metrics("linespace") + 2 * pad_y
        tab_w = icon_w + text_font.measure(label) + 2 * pad_x
        top = y0 - tab_h
        if top < feed_y0 + self._px(4):
            top = y0
        # Kept inside the feed - a face near the right edge would otherwise
        # push its tab off the side of the panel.
        left = max(feed_x0, min(x0 - self._px(1), feed_x1 - tab_w - self._px(4)))
        self.video_canvas.create_rectangle(left, top, left + tab_w, top + tab_h, fill=color, outline="")
        cy = top + tab_h / 2
        if glyph:
            self._canvas_text(left + pad_x, cy, glyph, ICON_FONT_BOLD, tab_px, "white")
        self._canvas_text(left + pad_x + icon_w, cy, label, FONT, tab_px, "white", weight="bold")

    def _handle_close(self):
        self._closed = True
        self._close_sign_in_dialog()
        if self._student is not None:
            self._student.close()  # it's a child window - it goes when the monitor does
        if self._on_close:
            self._on_close()
        self.window.destroy()
