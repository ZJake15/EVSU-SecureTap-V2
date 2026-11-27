import functools
import io
import math
import os
import queue
import time
import tkinter as tk
import traceback
import tkinter.font as tkfont
from collections import deque
from datetime import datetime
from tkinter import messagebox

import customtkinter as ctk
from PIL import Image, ImageColor, ImageDraw, ImageFont, ImageOps, ImageTk

from alert_sounds import AlertSounds, CoveredFaces
from lanes import LABELS as LANE_LABELS

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
# The least time between drawing one video frame and starting the next, so a
# computer too slow for the chosen smoothness still answers clicks and keys.
VIDEO_MIN_GAP_MS = 10

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
    # Settings > Scheduling > Gate opening hours
    "moon": "\ue330",
    # The Layout menu and its layouts (layouts.py)
    "layout": "\ue6d6",
    "caret-up": "\ue13c",
    "calendar-blank": "\ue10a",
    "arrows-left-right": "\ue0a0",
    "columns": "\ue546",
    "picture-in-picture": "\ue64c",
    "user": "\ue4c2",
    # Find person, the quick guide and the sound button (gate_tools.py)
    "magnifying-glass": "\ue30c",
    "question": "\ue3e8",
    "speaker-low": "\ue44c",
    "speaker-none": "\ue44e",
    "speaker-slash": "\ue45a",
    "play": "\ue3d0",
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


_RGB_CACHE = {}


def _rgb(color):
    """"#121416" -> (18, 20, 22), remembered - for filling numpy pixels."""
    if color not in _RGB_CACHE:
        _RGB_CACHE[color] = ImageColor.getrgb(color)[:3]
    return _RGB_CACHE[color]


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
# The window area of a maximized window on a 1080p screen (minus the title bar
# and taskbar) - what the redesign draws (docs/Redesign UI, Gate Monitor).
DESIGN_WIDTH, DESIGN_HEIGHT = 1920, 990
# Floor for that fit, so a small laptop screen shrinks the layout but never
# to the point of unreadable text.
MIN_DESIGN_FIT = 0.6

# Size profile, in design pixels: the full 1920x990 design ("WIDE") and its
# 1366x698 variant for a 768p laptop ("NARROW"). Every size in between is
# interpolated from the available width, so an un-maximized window still
# fits instead of overflowing at the full-screen sizes. The layout keys are
# for the Layout menu's views (layouts.py).
NARROW_WIDTH = 1366
NARROW_SIZES = {
    # header, stats, Split
    "header": 72, "pad": 24, "pad_t": 12, "pad_b": 12, "seal": 40, "title": 26, "sub": 13, "clock": 26,
    "date": 12, "badge": 20, "hero": 44, "stat": 34, "stat_label": 13, "stat_pad": 16, "col_gap": 20, "gap": 10,
    "log_title": 17, "strip": 38, "banner_title": 22, "banner_sub": 16, "feat_thumb": 88, "feat_name": 24,
    "feat_conf": 30, "feat_pad": 10, "band": 36, "feat_word": 17, "row_name": 19, "row_word": 16, "row_thumb": 56,
    "row_pad": 7, "meta": 13, "dept": 13, "tag": 16, "tab": 15, "cap": 12, "strip_f": 14, "strip_stat_l": 12,
    "strip_stat_v": 18,
    # the card reader's pill on the camera
    "tap_f": 20, "tap_i": 22, "tap_px": 14, "tap_py": 8, "tap_edge": 12,
    # banner bar (Verify wall, Entrance + Exit), thin stats row
    "banner_h": 46, "banner_t": 22, "banner_s": 16, "banner_i": 26, "thin_v": 22, "thin_gap": 22,
    # Verify wall
    "v_head": 38, "v_head_t": 17, "v_time": 13, "v_pad": 10, "v_gap": 12, "v_tile_w": 105, "v_tile_h": 140,
    "v_name": 21, "v_id": 14, "v_dept": 13, "pip_w": 236, "pip_h": 156, "pip_strip": 24, "pip_photo": 52,
    # Full camera
    "f_top": 48, "f_stat_v": 22, "f_banner": 60, "f_banner_t": 26, "f_card_w": 460, "f_photo": 84, "f_name": 24,
    "f_conf": 26, "tick_h": 76, "chip_p": 48, "chip_f": 14,
    # Filmstrip
    "film_h": 208, "film_w": 164, "film_gap": 10, "film_photo": 78, "film_name": 16, "film_bar": 28, "film_tag": 13,
    # Entrance + Exit
    "dual_h": 356, "inset_w": 300, "inset_h": 190, "m_row_thumb": 52, "m_row_name": 17, "m_pad": 7, "dir_w": 46,
    "dir_f": 15,
    # Compact rail
    "rail_w": 96, "rail_gap": 10, "av": 58, "ring": 4, "rail_n": 5, "rail_pop_w": 500,
    # status bar
    "foot_h": 44, "foot_f": 13,
}
WIDE_SIZES = {
    "header": 96, "pad": 42, "pad_t": 20, "pad_b": 16, "seal": 52, "title": 40, "sub": 16, "clock": 40,
    "date": 14, "badge": 26, "hero": 64, "stat": 52, "stat_label": 14, "stat_pad": 28, "col_gap": 42, "gap": 14,
    "log_title": 20, "strip": 48, "banner_title": 28, "banner_sub": 20, "feat_thumb": 150, "feat_name": 36,
    "feat_conf": 48, "feat_pad": 16, "band": 46, "feat_word": 22, "row_name": 26, "row_word": 20, "row_thumb": 80,
    "row_pad": 10, "meta": 16, "dept": 15, "tag": 20, "tab": 20, "cap": 14, "strip_f": 16, "strip_stat_l": 13,
    "strip_stat_v": 22,
    "tap_f": 26, "tap_i": 28, "tap_px": 20, "tap_py": 12, "tap_edge": 20,
    "banner_h": 56, "banner_t": 28, "banner_s": 20, "banner_i": 32, "thin_v": 30, "thin_gap": 34,
    "v_head": 48, "v_head_t": 22, "v_time": 16, "v_pad": 16, "v_gap": 22, "v_tile_w": 180, "v_tile_h": 240,
    "v_name": 30, "v_id": 18, "v_dept": 16, "pip_w": 340, "pip_h": 222, "pip_strip": 30, "pip_photo": 72,
    "f_top": 64, "f_stat_v": 30, "f_banner": 76, "f_banner_t": 34, "f_card_w": 640, "f_photo": 124, "f_name": 34,
    "f_conf": 36, "tick_h": 100, "chip_p": 64, "chip_f": 17,
    "film_h": 272, "film_w": 212, "film_gap": 12, "film_photo": 118, "film_name": 20, "film_bar": 36, "film_tag": 16,
    "dual_h": 556, "inset_w": 420, "inset_h": 262, "m_row_thumb": 64, "m_row_name": 22, "m_pad": 10, "dir_w": 56,
    "dir_f": 18,
    "rail_w": 124, "rail_gap": 14, "av": 78, "ring": 5, "rail_n": 6, "rail_pop_w": 640,
    "foot_h": 48, "foot_f": 14,
}


def _size_profile(design_width):
    t = max(0.0, min(1.0, (design_width - NARROW_WIDTH) / (DESIGN_WIDTH - NARROW_WIDTH)))
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


def _secondary_monitors():
    """[(left, top, right, bottom)] of every monitor but the main one, in real
    pixels, left to right - [] with one screen (or off Windows)."""
    try:
        import ctypes
        from ctypes import wintypes

        monitors = []
        callback_type = ctypes.WINFUNCTYPE(
            ctypes.c_int, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM
        )

        def collect(_monitor, _dc, rect, _data):
            r = rect.contents
            monitors.append((r.left, r.top, r.right, r.bottom))
            return 1

        ctypes.windll.user32.EnumDisplayMonitors(None, None, callback_type(collect), 0)
        # The main monitor is the one whose top-left corner is the origin.
        return sorted(m for m in monitors if (m[0], m[1]) != (0, 0))
    except Exception:
        return []


# The extra monitors in use by our own fullscreen windows (the student display
# and the exit camera's window), so two of them never land on the same one.
_CLAIMED_MONITORS = set()


def claim_secondary_monitor(last=False):
    """An extra monitor nobody of ours is using - the first one, or with
    last=True the last one - marked as taken; None if there's none free. Give
    it back with release_monitor when the window closes."""
    free = [m for m in _secondary_monitors() if m not in _CLAIMED_MONITORS]
    if not free:
        return None
    monitor = free[-1] if last else free[0]
    _CLAIMED_MONITORS.add(monitor)
    return monitor


def release_monitor(monitor):
    _CLAIMED_MONITORS.discard(monitor)


# Everything that can land in the live log, and how it reads there: status
# color, the tint behind a non-routine row, its icon, and its word. `sec`
# marks the security-relevant kinds that get a colored bar and tint in the
# list - a routine pass stays plain white.
LOG_KINDS = {
    "entry": {"color": VERIFIED, "icon": "sign-in", "word": "ENTRY"},
    # Someone recognized at the gate's exit camera (lanes.py) - leaving, so
    # plain ink rather than the entrance's green.
    "exit": {"color": INK_600, "icon": "sign-out", "word": "EXIT"},
    "card": {"color": VERIFIED, "icon": "identification-card", "word": "CARD"},
    # An odd entry/exit pattern (backend EntryLog.PatternFlag): leaving with no
    # entry today, or entering again with no exit since - usually a missed
    # scan, worth a question. Let in either way, no sound.
    "pattern": {"color": CAUTION, "tint": CAUTION_TINT, "icon": "warning", "word": "CHECK", "sec": True},
    "unknown": {"color": CAUTION, "tint": CAUTION_TINT, "icon": "user-circle-dashed", "word": "UNKNOWN", "sec": True},
    "spoof": {"color": DANGER, "tint": DANGER_TINT, "icon": "warning-octagon", "word": "SPOOF", "sec": True},
    "covered": {"color": PROMPT, "tint": PROMPT_TINT, "icon": "hand-palm", "word": "COVERED", "sec": True},
    "card_rejected": {"color": DANGER, "tint": DANGER_TINT, "icon": "identification-card", "word": "CARD REJECTED", "sec": True},
    "card_offline": {"color": CAUTION, "tint": CAUTION_TINT, "icon": "cloud-slash", "word": "QUEUED", "sec": True},
    # A recognized entry or card tap outside the gate's opening hours.
    "after_hours": {"color": CAUTION, "tint": CAUTION_TINT, "icon": "moon", "word": "AFTER HOURS", "sec": True},
    # A student let in who isn't on this semester's enrolled list (dashboard: Semester).
    "not_enrolled": {"color": CAUTION, "tint": CAUTION_TINT, "icon": "warning", "word": "NOT ENROLLED", "sec": True},
    # ...or refused for it (Settings > Semester Enrollment: "Refuse entry").
    "not_enrolled_refused": {"color": DANGER, "tint": DANGER_TINT, "icon": "warning-octagon",
                             "word": "NOT ENROLLED · REFUSED", "sec": True},
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


# The STUDENT / STAFF tag on a live-log row (Settings > Live Log Details).
KIND_COLORS = {"Student": MAROON, "Staff": INK_600}


def details_line(details):
    """ "School of Engineering · Civil Engineering Department · 3rd year" from
    the parts Settings > Live Log Details has on (backend users/live_details.py)."""
    return " · ".join(details[part] for part in ("unit", "department", "role") if (details or {}).get(part))


# The odd entry/exit patterns (backend EntryLog.PatternFlag) on a live-log
# row: (the row's word, the line under the name).
PATTERN_WORDS = {
    "no_entry": ("NO ENTRY TODAY", "no entry recorded today - ask them"),
    "no_exit": ("NO EXIT YET", "no exit since their last entry - ask them"),
}


class _Feed:
    """One camera's video panel on the gate monitor: the entrance camera's,
    and beside it the exit camera's when the gate has one (lanes.py). Each
    keeps its own picture, face boxes and caption; the alarm banner shows on
    the panel of the camera it's about. Sizes, fonts and the banner state
    come from the window (`monitor`).

    The canvas paints the whole panel itself - dark rounded body, the top
    strip (LIVE row or alarm banner) and the feed - as ONE composited image
    each frame, its corners rounded off in the canvas color (see
    compose_panel). Overlay text is drawn straight onto the canvas on top of
    that, never as CTkLabel widgets: a widget can't show the live video
    behind it, so any label over the feed would sit in its own flat box."""

    # How much of the camera frame may be trimmed off (per axis) so the feed
    # fills the panel edge to edge. Past this - a very tall or very wide
    # window - the whole frame is shown with bars instead, so nobody standing
    # near the edge of the picture can be cropped out of view.
    MAX_FEED_CROP = 0.35

    def __init__(self, monitor, parent, direction, get_preview_frame, get_camera_size=None, get_camera_fps=None,
                 camera_zoom=1.0, camera_index=None):
        self.m = monitor
        self.direction = direction
        self.canvas = tk.Canvas(parent, bg=CANVAS, highlightthickness=0)
        # A small inset (layouts.py): a thinner top strip, no caption,
        # smaller box labels - see setup().
        self.compact = False
        self.get_preview_frame = get_preview_frame
        # () -> (width, height) of the camera's own frames, before zoom and
        # before shrinking for the face checks; () -> frames a second it
        # really delivers, or None - both only for the caption.
        self.get_camera_size = get_camera_size
        self.get_camera_fps = get_camera_fps
        # The frames arrive already zoomed; this is only for the caption.
        self.camera_zoom = camera_zoom or 1.0
        self.camera_index = camera_index
        self.recognitions = []
        self.image_size = (1, 1)
        # Drives the "No camera connected" placeholder - a timestamp rather
        # than a sticky boolean, so the same logic covers never connected,
        # disconnected mid-session and reconnected. Starts now, not at zero,
        # so a camera's normal startup delay doesn't read as silent too long.
        self.last_frame_at = time.monotonic()
        self.showing_no_camera = False
        # The visible feed area on the canvas, (x0, y0, x1, y1) - face boxes
        # are clipped to it.
        self.feed_bounds = (0, 0, 1, 1)
        self.caption = "Starting camera…"
        self.draw_times = deque(maxlen=30)
        self._image = None
        self._cutouts_key = None
        self._cutouts = []
        # The camera's frame counter (Camera.frame_number), when the frames
        # come from a Camera - so an unchanged frame isn't drawn again.
        camera = getattr(get_preview_frame, "__self__", None)
        self._frame_number = getattr(camera, "frame_number", None)
        # Drawing only reads the pixels - from a Camera, without copying them
        # (Camera.peek_preview_frame).
        self._read_frame = getattr(camera, "peek_preview_frame", None) or get_preview_frame
        self._drawn_key = None  # what the picture on screen was drawn from (_draw_key)
        self._buffer = None  # the panel's pixels, reused frame to frame (compose_panel)
        self._drawn_rect = (0, 0, 1, 1)
        self._drawn_placement = None
        self.setup()

    def setup(self, compact=False, show_strip=True, show_banner=True, show_tap=False, strip_stats=None,
              overlay=None, radius=None, tap_bottom=0, background=CANVAS):
        """How this panel is drawn in the current layout (layouts.py):
        compact - a small inset (thin strip, short labels); show_strip - the
        LIVE row along the top; show_banner - the alarm in that strip (some
        layouts show it as a bar of their own); show_tap - the card reader's
        pill, bottom-right; strip_stats - [(label, () -> value, dot color)]
        after the camera's name; overlay - layouts.FullOverlay, drawn over
        the video; radius - corner rounding (0 = square); tap_bottom - room
        kept under the pill; background - what's behind the rounded corners."""
        self.compact = compact
        self.show_strip = show_strip
        self.show_banner = show_banner
        self.show_tap = show_tap and self.direction == "entry"
        self.strip_stats = strip_stats
        self.overlay = overlay
        self.radius = radius
        self.tap_bottom = tap_bottom
        self.background = background
        self.on_click = None

    def reparent(self, parent, background=CANVAS):
        """Moves this feed into another frame (a layout change, or the exit
        camera's own window): a fresh canvas there, drawn from the next frame."""
        self.canvas.destroy()
        self.canvas = tk.Canvas(parent, bg=background, highlightthickness=0, cursor="")
        self.canvas.bind("<Button-1>", lambda _e: self.on_click() if self.on_click else None)
        self._image = None
        self._cutouts_key = None
        self._drawn_key = None  # a new canvas - draw the next frame in full
        self.background = background

    def strip_height(self):
        if not self.show_strip and not self.banner:
            return 0
        return round(self.m._strip_height() * (0.62 if self.compact else 1))

    @property
    def alert(self):
        """The alarm banner, if it's about this camera."""
        alert = self.m._alert
        return alert if alert and alert.get("direction", "entry") == self.direction else None

    @property
    def banner(self):
        """The alarm, if it's about this camera and shows in its own strip."""
        return self.alert if self.show_banner else None

    def refresh(self):
        # The camera hasn't delivered a new frame since the last one drawn
        # (it runs slower than the screen redraws, e.g. in dim light): keep
        # the picture already on screen and redraw only what's on top of it.
        # Copying, shrinking and handing an unchanged 1-2 MP frame to Tk again
        # was pure cost - and it all competes with the face scanning for the
        # same CPU (and Python's GIL).
        number = self._frame_number() if self._frame_number else None
        if number is not None and not self.showing_no_camera and self._image is not None \
                and self._drawn_key == self._draw_key(number):
            self.last_frame_at = time.monotonic()
            self.draw_frame(None)
            return
        frame = self._read_frame()
        if frame is not None:
            self.last_frame_at = time.monotonic()
            self.showing_no_camera = False
            self.draw_frame(frame)
            self._drawn_key = self._draw_key(number) if number is not None else None
        elif time.monotonic() - self.last_frame_at >= self.m.CAMERA_GRACE_SECONDS:
            # No frame for a while now - either no camera ever connected, or
            # one did and then stopped (unplugged, driver crash - see
            # Camera._close_after_failure). Camera's own background loop
            # keeps retrying for as long as the app runs, so the moment a
            # frame arrives again the branch above takes back over.
            if not self.showing_no_camera:
                self.showing_no_camera = True
                self.m._refresh_in_frame()
            self._drawn_key = None
            self.draw_no_camera_placeholder()

    def _draw_key(self, number):
        """Everything baked into the drawn picture: the frame, the panel's
        size, the alarm color of the strip, and (Full camera) whether its
        person card has a band behind it."""
        banner = self.banner
        return (number, self.panel_size(), self.strip_height(), banner["kind"] if banner else None,
                bool(self.m._log_entries) if self.overlay is not None else None)

    def panel_size(self):
        return (self.canvas.winfo_width() or self.m.MIN_VIDEO_SIZE[0],
                self.canvas.winfo_height() or self.m.MIN_VIDEO_SIZE[1])

    def compose_panel(self, frame=None):
        """The whole video panel as one image, filling the canvas: dark body,
        the top strip (filled with the alarm color while a banner shows), and
        the live frame (OpenCV BGR) filling the area below it (cropped a
        little if its shape differs from the panel's - see MAX_FEED_CROP) -
        then the corners rounded off. Returns (image, panel rect, frame
        placement): the rect is (x, y, w, h) on the canvas; the placement is
        where the full, uncropped frame would sit, for mapping face boxes,
        which draw_box then clips to the visible feed.

        Built in one reused numpy buffer with OpenCV - shrunk, turned RGB
        only after shrinking, the corners painted from small precomputed
        masks - and turned into a PIL image once at the end."""
        import cv2  # already loaded by the camera; here so the launcher's `from ui import` doesn't load it
        import numpy as np

        canvas_w, canvas_h = self.panel_size()
        rect = (0, 0, canvas_w, canvas_h)
        strip_h = min(self.strip_height(), canvas_h)
        feed_w, feed_h = canvas_w, max(1, canvas_h - strip_h)
        self.feed_bounds = (0, strip_h, feed_w, strip_h + feed_h)
        if self._buffer is None or self._buffer.shape[:2] != (canvas_h, canvas_w):
            self._buffer = np.empty((canvas_h, canvas_w, 3), np.uint8)
        panel = self._buffer
        # Only what the picture won't cover is painted, with OpenCV's filled
        # rectangle - numpy's `panel[:] = (r, g, b)` took 11 ms at 1920x850.
        covered = False
        placement = None
        if frame is not None:
            src_h, src_w = frame.shape[:2]
            cover = max(feed_w / src_w, feed_h / src_h)
            crop = 1 - min(feed_w / (src_w * cover), feed_h / (src_h * cover))
            scale = cover if crop <= self.MAX_FEED_CROP else min(feed_w / src_w, feed_h / src_h)
            img_w, img_h = max(1, round(src_w * scale)), max(1, round(src_h * scale))
            origin_x, origin_y = (feed_w - img_w) // 2, strip_h + (feed_h - img_h) // 2
            # Only the part inside the feed area is shrunk and pasted, so a
            # cropped frame can't spill up into the strip. Bilinear, not
            # Lanczos: on a moving video frame the difference isn't visible,
            # and it's several times cheaper every frame.
            left, top = max(0, -origin_x), max(0, strip_h - origin_y)
            right, bottom = min(img_w, feed_w - origin_x), min(img_h, strip_h + feed_h - origin_y)
            if right > left and bottom > top:
                x0, y0 = max(0, origin_x), max(strip_h, origin_y)
                covered = (x0, y0, right - left, bottom - top) == (0, strip_h, feed_w, feed_h)
                if not covered:
                    cv2.rectangle(panel, (0, 0), (canvas_w - 1, canvas_h - 1), _rgb(INK), -1)
                resized = cv2.resize(frame, (img_w, img_h), interpolation=cv2.INTER_LINEAR)
                panel[y0:y0 + bottom - top, x0:x0 + right - left] = cv2.cvtColor(
                    resized[top:bottom, left:right], cv2.COLOR_BGR2RGB)
            else:
                cv2.rectangle(panel, (0, 0), (canvas_w - 1, canvas_h - 1), _rgb(INK), -1)
            placement = (origin_x, origin_y, img_w, img_h)
        if placement is None:
            cv2.rectangle(panel, (0, 0), (canvas_w - 1, canvas_h - 1), _rgb(INK), -1)
        if strip_h:
            strip_color = ALERTS[self.banner["kind"]]["color"] if self.banner else INK
            cv2.rectangle(panel, (0, 0), (canvas_w - 1, strip_h - 1), _rgb(strip_color), -1)
        if self.overlay is not None:
            self.overlay.paint(self, panel)
        radius = self.m.VIDEO_PANEL_CORNER_RADIUS if self.radius is None else self.radius
        if radius:
            background = _rgb(self.background)
            for (y0, y1, x0, x1), outside in self.corner_cutouts((canvas_w, canvas_h), self.m._px(radius)):
                panel[y0:y1, x0:x1][outside] = background
        return Image.fromarray(panel), rect, placement

    def corner_cutouts(self, size, radius):
        """The four corners outside the rounded panel, as ((y0, y1, x0, x1),
        boolean mask) - built once per panel size and painted each frame."""
        import numpy as np

        key = (size, radius)
        if self._cutouts_key != key:
            mask = Image.new("L", size, 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
            outside = np.asarray(mask) == 0
            width, height = size
            r = max(1, min(radius, width, height))
            boxes = ((0, r, 0, r), (0, r, width - r, width), (height - r, height, 0, r),
                     (height - r, height, width - r, width))
            self._cutouts_key = key
            self._cutouts = [(box, outside[box[0]:box[1], box[2]:box[3]].copy()) for box in boxes]
        return self._cutouts

    def show_panel(self, panel, rect):
        # Kept as self._image (not a local) - Tk drops a PhotoImage with no
        # surviving reference, which would blank the canvas. Reused (pasted
        # into) while the panel keeps its size.
        image = self._image
        if image is not None and (image.width(), image.height()) == panel.size:
            image.paste(panel)
        else:
            self._image = ImageTk.PhotoImage(panel)
        self._show_image(rect)

    def _show_image(self, rect):
        """The canvas, emptied, with the current picture on it - what's drawn
        on top (boxes, strip, pill) is drawn again after this."""
        self.canvas.delete("all")
        self.canvas.create_image(rect[0], rect[1], image=self._image, anchor="nw")

    def text(self, x, y, text, family, px, color, anchor="w", weight="normal", width=None):
        kwargs = {"width": width} if width else {}
        return self.canvas.create_text(
            x, y, text=text, font=self.m._tkfont(family, px, weight), fill=color, anchor=anchor, **kwargs
        )

    def draw_overlays(self, rect):
        """Everything drawn over the picture, on every redraw (so a banner's
        appear/disappear timing needs no redraw call of its own): the top
        strip, the card reader's pill, and the layout's own overlay."""
        if self.show_strip or self.banner:
            self.draw_strip(rect)
        if self.show_tap:
            self.draw_tap()
        if self.overlay is not None:
            self.overlay.draw(self, rect)

    def draw_tap(self):
        """The card reader's state as a pill, bottom-right of the picture:
        "Tap a card", "Card accepted · name", "Card not registered"... (the
        redesign's replacement for the card scanner strip)."""
        m = self.m
        s = m._s
        tap = m._tap
        k = 0.6 if self.compact else 1
        font_px = round(s["tap_f"] * k)
        icon_px = round(s["tap_i"] * k)
        feed_x0, _feed_y0, feed_x1, feed_y1 = self.feed_bounds
        font = m._tkfont(SEMI_HEAVY, font_px)
        glyph = _icon(tap["icon"])
        icon_w = m._px(icon_px + 10) if glyph else 0
        pad_x, pad_y = m._px(s["tap_px"] * k), m._px(s["tap_py"] * k)
        text = _ellipsize(font, tap["text"], (feed_x1 - feed_x0) * 0.7 - icon_w - 2 * pad_x)
        width = icon_w + font.measure(text) + 2 * pad_x
        height = max(font.metrics("linespace"), m._px(icon_px)) + 2 * pad_y
        x1 = feed_x1 - m._px(s["tap_edge"] * k)
        y1 = feed_y1 - m._px(s["tap_edge"] * k + self.tap_bottom)
        self.canvas.create_polygon(_rounded_rect_points(x1 - width, y1 - height, x1, y1, m._px(8)),
                                   fill=tap["color"], outline="")
        cy = y1 - height / 2
        if glyph:
            self.text(x1 - width + pad_x, cy, glyph, ICON_FONT_BOLD, icon_px, "white")
        self.text(x1 - width + pad_x + icon_w, cy, text, SEMI_HEAVY, font_px, "white")

    def draw_strip(self, rect):
        """The top strip's contents - either the LIVE row (a square status
        dot, LIVE / NO SIGNAL, which camera, the layout's counters and the
        caption) or the alarm banner's icon, title, subtitle, speaker mark
        and time."""
        m = self.m
        s = m._s
        k = 0.62 if self.compact else 1  # the small inset's strip is thinner
        panel_x, panel_y, panel_w, _panel_h = rect
        strip_h = self.strip_height()
        cy = panel_y + strip_h / 2
        x = panel_x + m._px(26 * k)
        alert_state = self.banner
        if alert_state:
            alert = ALERTS[alert_state["kind"]]
            glyph = _icon(alert["icon"])
            if glyph:
                self.text(x, cy, glyph, ICON_FONT_BOLD, 36 * k * s["strip"] / 64, "white")
                x += m._px((36 * s["strip"] / 64 + 16) * k)
            # Right side first - the time always shows, the speaker mark when
            # there's room - so the title and subtitle fit into what's left
            # (a half-width panel, with two cameras, has far less).
            right_x = panel_x + panel_w - m._px(26)
            time_id = self.text(right_x, cy, alert_state["time"], FONT_MONO_MEDIUM, 16, "white", anchor="e")
            right_x = self.canvas.bbox(time_id)[0] - m._px(12)
            speaker = _icon("speaker-high")
            if speaker and right_x - x > m._px(520):
                speaker_id = self.text(right_x, cy, speaker, ICON_FONT_BOLD, 28 * s["strip"] / 64, "white", anchor="e")
                right_x = self.canvas.bbox(speaker_id)[0] - m._px(16)
            title = alert["title"] + (" · EXIT" if self.direction == "exit" else "")
            # The title's size steps down until it fits, then is shortened if
            # it still doesn't.
            title_px = round(s["banner_title"] * k)
            while title_px > 12 and m._tkfont(SEMI_HEAVY, title_px).measure(title) > right_x - x - m._px(8):
                title_px -= 2
            title = _ellipsize(m._tkfont(SEMI_HEAVY, title_px), title, right_x - x - m._px(8))
            title_id = self.text(x, cy, title, SEMI_HEAVY, title_px, "white")
            x = self.canvas.bbox(title_id)[2] + m._px(16)
            sub_font = m._tkfont(FONT, s["banner_sub"])
            sub = _ellipsize(sub_font, alert_state.get("sub") or alert["sub"], right_x - x)
            if sub and right_x - x > m._px(40) and not self.compact:
                self.text(x, cy, sub, FONT, s["banner_sub"], "white")
            return

        live = not self.showing_no_camera
        dot = m._px(10 * k)
        word_px = 13 if self.compact else 16
        self.canvas.create_rectangle(x, cy - dot / 2, x + dot, cy + dot / 2, fill=BRASS if live else INK_400,
                                     outline="")
        word_id = self.text(x + dot + m._px(10 * k), cy, "LIVE" if live else "NO SIGNAL", COND_HEAVY, word_px,
                            "white")
        x = self.canvas.bbox(word_id)[2] + m._px(12 * k)
        # Which camera this is - always named, so a guard never has to guess
        # which side of the gate a face is on.
        lane_id = self.text(x, cy, LANE_LABELS.get(self.direction, ""), COND_HEAVY, word_px, BRASS)
        if self.compact:
            return  # no room for the caption on the small inset
        x = self.canvas.bbox(lane_id)[2] + m._px(16)
        right = panel_x + panel_w - m._px(16)
        # The layout's counters for this camera (Entrance + Exit, Compact rail).
        for label, value, dot_color in self.strip_stats or ():
            if right - x < m._px(260):
                break
            self.canvas.create_line(x, cy - m._px(10), x, cy + m._px(10), fill=INK_600)
            x += m._px(14)
            self.canvas.create_rectangle(x, cy - m._px(4), x + m._px(8), cy + m._px(4), fill=dot_color, outline="")
            item = self.text(x + m._px(15), cy, label, COND_BOLD, s["strip_stat_l"], LINE)
            item = self.text(self.canvas.bbox(item)[2] + m._px(7), cy, value(), SEMI_HEAVY, s["strip_stat_v"],
                             "white")
            x = self.canvas.bbox(item)[2] + m._px(14)
        caption_font = m._tkfont(FONT_MONO, s["cap"])
        caption = _ellipsize(caption_font, self.caption, right - x)
        if right - x > m._px(40):
            self.text(right, cy, caption, FONT_MONO, s["cap"], INK_400, anchor="e")

    def draw_no_camera_placeholder(self):
        m = self.m
        s = m._s
        panel, rect, _placement = self.compose_panel(None)
        self.show_panel(panel, rect)
        panel_x, panel_y, panel_w, panel_h = rect
        strip_h = self.strip_height()
        cx, cy = panel_x + panel_w / 2, panel_y + strip_h + (panel_h - strip_h) / 2
        index = self.camera_index if self.camera_index is not None else 0
        if self.compact:
            self.text(cx, cy, "No camera", SEMI_HEAVY, 16, "white", anchor="center")
            self.caption = f"Camera {index} · waiting for device"
            self.draw_overlays(rect)
            return
        t = (s["strip"] - 52) / 12  # 0 at the narrow profile, 1 at full HD
        glyph = _icon("video-camera-slash", bold=False)
        if glyph:
            self.text(cx, cy - m._px(48), glyph, ICON_FONT, 44 + 12 * t, BRASS, anchor="s")
        wrap = min(panel_w - m._px(52), m._px(620))
        self.text(cx, cy - m._px(28), "No camera connected — card taps still work.", SEMI_HEAVY, 22 + 6 * t,
                  "white", anchor="n", width=wrap)
        self.text(cx, cy + m._px(28 + 14 * t), "Connecting a camera resumes automatically.", FONT, 16 + 4 * t,
                  LINE, anchor="n", width=wrap)
        index = self.camera_index if self.camera_index is not None else 0
        self.caption = f"Camera {index} · waiting for device"
        self.draw_overlays(rect)

    def draw_frame(self, frame):
        """A new camera frame - or, with frame None, the picture already
        drawn, with the boxes, strip and pill drawn again on top of it."""
        if frame is None:
            rect, placement = self._drawn_rect, self._drawn_placement
            self._show_image(rect)
        else:
            panel, rect, placement = self.compose_panel(frame)
            self.show_panel(panel, rect)
            self._drawn_rect, self._drawn_placement = rect, placement
            # How smooth the video really is: new pictures a second.
            self.draw_times.append(time.monotonic())

        src_w, src_h = self.image_size
        if src_w > 0 and src_h > 0 and placement:
            origin_x, origin_y, img_w, img_h = placement
            for item in self.recognitions:
                self.draw_box(item, origin_x, origin_y, img_w, img_h, src_w, src_h)

        count = len(self.recognitions)
        zoom = f" · zoom {self.camera_zoom:.1f}x" if self.camera_zoom > 1.0 else ""
        # Two sizes, so 1440p doesn't look like it isn't working: what the
        # camera takes, and what the face checks get (zoomed, then shrunk to
        # Settings > Camera > "Detail sent for face checks").
        camera_size = self.get_camera_size() if self.get_camera_size else None
        camera_fps = self.get_camera_fps() if self.get_camera_fps else None
        camera = (f"camera {camera_size[0]}×{camera_size[1]}" + (f" {camera_fps:.0f} fps" if camera_fps else "")
                  + " · ") if camera_size else ""
        checked = f"checked {src_w}×{src_h}" if src_w > 1 and src_h > 1 else "checked –"
        # New pictures a second (counted above) - it can't look smoother than
        # the camera, and a frame drawn once isn't counted again.
        times = self.draw_times
        screen = (f" · screen {(len(times) - 1) / (times[-1] - times[0]):.0f} fps"
                  if len(times) >= 10 and times[-1] > times[0] else "")
        self.caption = f"{camera}{checked}{zoom}{screen} · {count} face{'s' if count != 1 else ''} tracked"
        self.draw_overlays(rect)

    def draw_box(self, item, origin_x, origin_y, img_w, img_h, src_w, src_h):
        m = self.m
        box = item.get("box")
        if not box:
            return
        x0 = origin_x + (box["left"] / src_w) * img_w
        y0 = origin_y + (box["top"] / src_h) * img_h
        x1 = origin_x + (box["right"] / src_w) * img_w
        y1 = origin_y + (box["bottom"] / src_h) * img_h
        # Clipped to the visible feed - the frame may be cropped a little to
        # fill the panel (see compose_panel), and a face in the trimmed
        # margin shouldn't draw its box over the strip or off the panel.
        feed_x0, feed_y0, feed_x1, feed_y1 = self.feed_bounds
        x0, y0 = max(x0, feed_x0), max(y0, feed_y0)
        x1, y1 = min(x1, feed_x1 - 1), min(y1, feed_y1 - 1)
        if x1 - x0 < m._px(8) or y1 - y0 < m._px(8):
            return  # face is (almost) entirely in the cropped-off margin
        color, icon_name, label = GateMonitorWindow._box_style(item)
        if item.get("distance_m") is not None:
            label = f"{label} · {item['distance_m']:.1f} m"

        self.canvas.create_rectangle(x0, y0, x1, y1, outline=color, width=m._px(2))

        # The name tab: a solid color tab sitting on the box's top edge,
        # icon + label in white. Flipped inside the box if it would run up
        # into the top strip.
        tab_px = round(m._s["tab"] * (0.7 if self.compact else 1))
        text_font = m._tkfont(FONT, tab_px, "bold")
        glyph = _icon(icon_name)
        icon_w = m._px(tab_px + 6) if glyph else 0
        pad_x, pad_y = m._px(10), m._px(6)
        tab_h = text_font.metrics("linespace") + 2 * pad_y
        tab_w = icon_w + text_font.measure(label) + 2 * pad_x
        top = y0 - tab_h
        if top < feed_y0 + m._px(4):
            top = y0
        # Kept inside the feed - a face near the right edge would otherwise
        # push its tab off the side of the panel.
        left = max(feed_x0, min(x0 - m._px(1), feed_x1 - tab_w - m._px(4)))
        self.canvas.create_rectangle(left, top, left + tab_w, top + tab_h, fill=color, outline="")
        cy = top + tab_h / 2
        if glyph:
            self.text(left + pad_x, cy, glyph, ICON_FONT_BOLD, tab_px, "white")
        self.text(left + pad_x + icon_w, cy, label, FONT, tab_px, "white", weight="bold")


class _ExitWindow:
    """The "Separate window" layout: the exit camera in a window of its own,
    for a gate with more than one monitor - fullscreen on the last extra
    monitor (the student display takes the first, ui.claim_secondary_monitor),
    or a normal window to drag there with just one screen. F11, Escape or a
    double-click on the feed toggle fullscreen.

    Beside the feed, its own live log of what happened at the exit, and the
    exit counters in its header; the main window keeps the entrance and its
    log. Closing it puts the exit camera back beside the entrance (main
    window, side by side)."""

    LOG_ROWS = 40

    def __init__(self, monitor, on_close):
        self.m = monitor
        self._on_close = on_close
        self._closed = False
        self._rows = []  # newest first
        self.window = ctk.CTkToplevel(monitor.window)
        self.window.title(f"EVSU SecureTap - Exit camera - {monitor.gate_location}")
        self.window.configure(fg_color=CANVAS)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.after(250, lambda: _apply_icon(self.window))  # CTkToplevel sets its own icon first

        header = ctk.CTkFrame(self.window, fg_color=MAROON, corner_radius=0, height=64)
        header.pack(fill="x")
        header.pack_propagate(False)
        monitor._icon_label(header, "sign-out", 26, "white", fg_color=MAROON).pack(side="left", padx=(24, 10))
        monitor._label(header, f"EXIT · {monitor.gate_location}", (SEMI_HEAVY, 24), "white").pack(side="left")
        self._counts = monitor._label(header, "", (FONT_MONO_MEDIUM, 16), "white")
        self._counts.pack(side="right", padx=24)
        tk.Frame(self.window, bg=BRASS, height=3).pack(fill="x")

        body = ctk.CTkFrame(self.window, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=16, pady=16)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)
        self.feed_area = ctk.CTkFrame(body, fg_color="transparent")
        self.feed_area.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        self.feed_area.grid_rowconfigure(0, weight=1)
        self.feed_area.grid_columnconfigure(0, weight=1)
        log = ctk.CTkFrame(body, fg_color="transparent", width=380)
        log.grid(row=0, column=1, sticky="ns")
        log.pack_propagate(False)
        monitor._label(log, "Exit log", (SEMI_BOLD, 20), INK, anchor="w").pack(fill="x")
        self._list = ctk.CTkScrollableFrame(
            log, fg_color=SURFACE, corner_radius=8, border_width=1, border_color=LINE,
            scrollbar_button_color=SURFACE, scrollbar_button_hover_color=LINE,
        )
        self._list.pack(fill="both", expand=True, pady=(8, 0))
        self._placeholder = monitor._label(self._list, "Exits appear here.", (FONT, 14), INK_400)
        self._placeholder.pack(anchor="w", padx=14, pady=14)

        # A card tap typed while this window has the keyboard still reaches
        # the gate monitor's card field (same as the student display).
        self.window.bind("<Key>", monitor._forward_card_key)
        self.window.bind("<F11>", lambda _e: self.toggle_fullscreen())
        self.window.bind("<Escape>", lambda _e: self.set_fullscreen(False))
        self._monitor = claim_secondary_monitor(last=True)
        if self._monitor:
            left, top, right, bottom = self._monitor
            self.window.geometry(f"{right - left}x{bottom - top}+{left}+{top}")
            self.window.after(300, lambda: self.set_fullscreen(True))
        else:
            work_w, _work_h = _screen_work_area(self.window)
            width = round(work_w * 0.55 / monitor._scale)
            self.window.geometry(f"{width}x{round(width * 0.62)}+80+80")

    def set_fullscreen(self, on):
        if not self._closed:
            self.window.attributes("-fullscreen", bool(on))

    def toggle_fullscreen(self):
        if not self._closed:
            self.set_fullscreen(not self.window.attributes("-fullscreen"))

    def owns(self, widget):
        return widget is not None and not self._closed and str(widget).startswith(str(self.window))

    def set_counts(self, exits, unknown, spoof):
        self._counts.configure(text=f"EXITS {exits}  ·  UNKNOWN {unknown}  ·  SPOOF {spoof}")

    def add_row(self, entry):
        """One exit-camera or exit-reader event, newest at the top."""
        m = self.m
        if self._placeholder is not None:
            self._placeholder.destroy()
            self._placeholder = None
        view = LOG_KINDS[entry["kind"]]
        bg = view.get("tint", SURFACE)
        box = tk.Frame(self._list, bg=SURFACE)
        row = ctk.CTkFrame(box, fg_color=bg, corner_radius=0)
        row.pack(fill="x")
        row.grid_columnconfigure(1, weight=1)
        ctk.CTkFrame(row, fg_color=view["color"] if view.get("sec") else bg, width=3, height=1,
                     corner_radius=0).grid(row=0, column=0, sticky="ns")
        text = ctk.CTkFrame(row, fg_color=bg, corner_radius=0)
        text.grid(row=0, column=1, sticky="ew", padx=(12, 8), pady=8)
        name_font = m._tkfont(FONT, 18, "bold")
        meta_font = m._tkfont(FONT_MONO_MEDIUM, 13)
        width = m._px(230)
        m._label(text, _ellipsize(name_font, entry["name"], width), (FONT, 18, "bold"), INK, anchor="w",
                 fg_color=bg).pack(fill="x")
        details = ctk.CTkFrame(text, fg_color=bg, corner_radius=0)
        m._show_details(entry.get("details"), details,
                        m._label(details, "", (COND_HEAVY, 12), "white", corner_radius=3, height=18),
                        m._label(details, "", (FONT, 13), INK_600, anchor="w", fg_color=bg), width, (FONT, 13))
        m._label(text, _ellipsize(meta_font, m._meta(entry), width), (FONT_MONO_MEDIUM, 13), INK_600, anchor="w",
                 fg_color=bg).pack(fill="x")
        word = ctk.CTkFrame(row, fg_color=bg, corner_radius=0)
        word.grid(row=0, column=2, sticky="e", padx=(4, 12))
        m._icon_label(word, view["icon"], 15, view["color"], fg_color=bg).pack(side="left", padx=(0, 4))
        m._label(word, entry["word"].replace("CARD · ", "").replace(" · EXIT", ""), (COND_HEAVY, 15),
                 view["color"], fg_color=bg).pack(side="left")
        tk.Frame(box, bg=LINE, height=max(1, m._px(1))).pack(fill="x")
        if self._rows:
            box.pack(fill="x", before=self._rows[0])
        else:
            box.pack(fill="x")
        self._rows.insert(0, box)
        for old in self._rows[self.LOG_ROWS:]:
            old.destroy()
        self._rows = self._rows[: self.LOG_ROWS]

    def close(self, notify=True):
        if self._closed:
            return
        self._closed = True
        release_monitor(self._monitor)
        try:
            self.window.destroy()
        except tk.TclError:
            pass
        if notify and self._on_close:
            self._on_close()


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
    REBUILD_ROWS = 25  # rows drawn when the Split view is (re)built
    ALERT_DISPLAY_MS = 6000

    FAILURE_HEADLINES = {
        "not_registered": "Not registered",
        "deactivated": "Deactivated",
        # Settings > Semester Enrollment: "Refuse entry".
        "not_enrolled": "Not enrolled this semester",
        "read_error": "Read error",
        "offline": "Offline",
        "server_error": "Server error",
        # A guard's own staff ID card (Settings -> "Guards sign in at the gate
        # monitor") that couldn't sign them in - the reason is shown below.
        "staff_sign_in_off": "Staff card",
        "staff_not_allowed": "Can't sign in here",
        "staff_replayed": "Staff card",
        "staff_card_off": "Staff card",
    }

    # (key, label, color, icon) - "today" is the hero number, the rest sit
    # beside it separated by hairlines. Covered faces are still counted
    # (self.stats["occlusion"], used for the last-session summary) but have
    # no counter on screen.
    STAT_SPECS = (
        ("entries", "ENTRIES", INK, "sign-in"),
        # People recognized at the exit camera (lanes.py) - 0 at a gate without one.
        ("exits", "EXITS", INK_600, "sign-out"),
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
                 video_fps=None, student_display_fps=None, on_sign_in=None, on_sign_out=None, camera_zoom=1.0,
                 get_camera_size=None, get_camera_fps=None, on_exit_camera_change=None, on_reader_setup=None,
                 layout="split", on_layout_change=None, on_face_scan=None, dual_mode="side",
                 on_refresh_cameras=None, sound_volume=1.0, on_sound_change=None, on_find_person=None,
                 on_let_in=None):
        # on_refresh_cameras() -> (options, entrance index, exit index) or
        # None: main.py lists the cameras again when "Cameras & card readers"
        # opens, so one plugged in since the start can be picked.
        self._on_refresh_cameras = on_refresh_cameras
        # The helper windows (gate_tools.py), while open: Find person -
        # on_find_person(query, done) and on_let_in(person, direction,
        # reason, done) are main.py's - the quick guide ("?"), and the sound
        # menu, whose volume main.py remembers through on_sound_change(volume).
        self._on_find_person = on_find_person
        self._on_let_in = on_let_in
        self._on_sound_change = on_sound_change
        self._find_window = None
        self._guide_window = None
        self._sound_menu = None
        self._sound_button = None
        self._sound_tick_job = None
        # The screen layout (LAYOUTS, layouts.py) - picked from the Layout
        # menu in the status bar, remembered by main.py through
        # on_layout_change(layout, dual_mode). A value saved before the
        # Layout menu existed maps onto its nearest new layout.
        layout, dual_mode = self.OLD_LAYOUTS.get(layout, (layout, dual_mode))
        self._layout = layout if layout in self.LAYOUTS else "split"
        # Entrance + Exit: "side" (side by side) or "big" (big + small).
        self._dual_mode = dual_mode if dual_mode in ("side", "big") else "side"
        self._on_layout_change = on_layout_change
        # With an exit camera: which one is big and which the small inset.
        self._inset_big = "entry"
        # Verify wall: None (the cards), or the camera shown big instead.
        self._verify_swap = None
        self._view = None  # the current layout's view (layouts.py)
        self._layout_menu = None  # the Layout menu, while open
        self._alert_key = None  # the alarm the view last heard about
        self._in_frame_text = "0"
        # The card reader's pill on the entrance camera (_Feed.draw_tap).
        self._tap = {"text": "Tap a card", "icon": "identification-card", "color": INK}
        self._left_column = None
        self._exit_window = None  # the "Separate window" layout's _ExitWindow, while open
        self.exit_stats = {"unknown": 0, "spoof": 0}  # for the exit window's header
        # The entrance camera's feed - get_preview_frame, its camera zoom (the
        # frames arrive already zoomed; only for the caption), and () -> its
        # frame size / measured frame rate, also for the caption. The exit
        # camera's feed is added later by show_exit_feed (see _Feed).
        self._entry_feed_args = dict(get_preview_frame=get_preview_frame, get_camera_size=get_camera_size,
                                     get_camera_fps=get_camera_fps, camera_zoom=camera_zoom,
                                     camera_index=initial_camera_index)
        self._feeds = {}
        # on_exit_camera_change(index or None): the guard chose the exit
        # camera (or "none") in the "Cameras & card readers" window - main.py
        # opens it and calls show_exit_feed / hide_exit_feed.
        self._on_exit_camera_change = on_exit_camera_change
        self._exit_camera_index = None
        # on_reader_setup("set_exit" | "clear"): the same window's exit card
        # reader buttons (card_readers.py); set_reader_status() answers.
        self._on_reader_setup = on_reader_setup
        self._readers = {"available": False, "exit_reader": None, "waiting": False}
        self._setup_dialog = None
        # Outside the gate's opening hours (Settings > Scheduling), as the
        # backend says with every scan answer - see set_after_hours().
        self._after_hours = False
        # The capstone test mode (test_mode.py) - see set_test_mode().
        self._test_mode = False
        # Other windows whose text boxes may keep the keyboard - see
        # allow_typing_in().
        self._typing_windows = []
        # Guard sign-in (Settings -> "Guards sign in at the gate monitor"):
        # on_sign_in(username, password) and on_sign_out(shift_id) are main.py's,
        # run off the Tk thread; the answers come back through
        # set_gate_sign_in() / show_sign_in_result().
        self._on_sign_in = on_sign_in
        self._on_sign_out = on_sign_out
        self._sign_in = {"enabled": False, "on_duty": None}
        self._sign_in_dialog = None
        # Signing in by face (Settings -> "Sign in with face scan"):
        # on_face_scan(True, pending) starts main.py sending the entrance
        # camera's frames to the backend, on_face_scan(False, None) stops it;
        # each answer comes back through show_face_sign_in(). _face_step is
        # {"name", "pending"} after a staff card tap with "Card, then face".
        self._on_face_scan = on_face_scan
        self._face_step = None
        self._face_scanning = False
        self._face_view = None
        self._face_photo = None
        self._face_preview_job = None
        self._dialog_ways = None
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
        # which one to actually use; see _open_setup_dialog.
        # on_camera_change(index) is main.py's, for the entrance camera.
        self._camera_options = camera_options or []
        self._on_camera_change = on_camera_change

        self._seen_log_ids = set()
        self._log_entries = []  # newest first; each keeps its own row widgets and thumbnails (_add_log_row)
        self._log_placeholder = None  # "Earlier events appear here." while the list is empty
        self.stats = {"entries": 0, "exits": 0, "unknown": 0, "spoof": 0, "occlusion": 0}
        # {"kind": ..., "time": ..., "sub": ..., "direction": ...} while the
        # alarm banner shows - on the panel of the camera it's about.
        self._alert = None
        # Settings page: "Alert on suspected fake face" - on until the
        # backend says otherwise (see set_alert_settings).
        self._alert_spoof = True
        # Settings > Alerts: a sound switch each for unknown, covered and
        # not-enrolled (refused) - all on until the backend says otherwise.
        self._sound_on = {"unknown": True, "covered": True, "not_enrolled": True}
        # A different sound per event (alert_sounds.py), as loud as this
        # computer's sound button last said.
        self._sounds = AlertSounds(volume=sound_volume)
        # Once per covered person - kept per camera (their boxes are in
        # different pictures).
        self._covered_faces = {"entry": CoveredFaces(), "exit": CoveredFaces()}
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
        # Closing stops the gate's scanning and signs the guard on duty out -
        # asked first, so a stray Escape can't do it (_confirm_close).
        self.window.protocol("WM_DELETE_WINDOW", self._confirm_close)
        self.window.bind("<Escape>", lambda _e: self._confirm_close())
        _apply_icon(self.window)

        work_width = self._fit_design_to_screen()
        self._scale = ctk.ScalingTracker.get_widget_scaling(self.window)
        self._s = _size_profile(work_width / self._scale)

        self.window.grid_columnconfigure(0, weight=1)
        self.window.grid_rowconfigure(2, weight=1)

        self._build_header()
        self._build_body()
        self._build_status_bar()

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
        self._label(wordmark, "EVSU", (WIDE_BLACK, 13), "white", height=15).pack(anchor="w")
        self._label(wordmark, "SecureTap", (SEMI_BOLD, 18), "white", height=21).pack(anchor="w")

        # Plain tk.Frame - a 1px CTkFrame draws nothing, which is why this
        # divider used to be missing.
        tk.Frame(header, bg=BRASS, width=max(1, self._px(1))).pack(side="left", fill="y", pady=20)

        # Right side packs right-to-left: the ENTRY pill ("ENTRY + EXIT" once
        # the gate has an exit camera), then the clock. Turns into a caution
        # "AFTER HOURS" pill outside the gate's opening hours - see
        # _render_mode_badge.
        badge = ctk.CTkFrame(header, fg_color=SURFACE, corner_radius=round(s["badge"] * 1.1))
        badge.pack(side="right", padx=(26, s["pad"]))
        self._mode_icon = self._icon_label(badge, "sign-in", round(s["badge"] * 1.0), MAROON_DEEP)
        self._mode_icon.pack(side="left", padx=(18, 8), pady=8)
        self._mode_word = self._label(badge, "ENTRY", (WIDE_HEAVY, s["badge"]), MAROON_DEEP)
        self._mode_word.pack(side="left", padx=(0, 22), pady=8)
        self._mode_badge = badge

        clock = ctk.CTkFrame(header, fg_color="transparent")
        clock.pack(side="right")
        self.clock_label = self._label(clock, "", (FONT_MONO_MEDIUM, s["clock"]), "white", height=s["clock"] + 4)
        self.clock_label.pack(anchor="e")
        self.date_label = self._label(clock, "", (FONT_MONO, s["date"]), BRASS, height=s["date"] + 4)
        self.date_label.pack(anchor="e")

        title = ctk.CTkFrame(header, fg_color="transparent")
        title.pack(side="left", fill="x", expand=True, padx=(26, 26))
        self._title_full = f"{self.gate_location} — live monitoring"
        self._title_label = self._label(
            title, self._title_full, (SEMI_HEAVY, s["title"]), "white", anchor="w", height=s["title"] + 6,
        )
        self._title_label.pack(fill="x", anchor="w")
        self._header_sub = self._label(title, "Face recognition + NFC card", (FONT, s["sub"]), "white", anchor="w",
                                       height=s["sub"] + 4)
        self._header_sub.pack(fill="x", anchor="w")
        self._title_frame = title
        self._title_width = None
        title.bind("<Configure>", self._fit_header_title)

    def _build_bar_tools(self, bar):
        """Two small buttons in the status bar, right after who's on duty:
        the alert sounds' volume / mute (gate_tools.SoundMenu) and the quick
        guide, "?"."""
        tools = ctk.CTkFrame(bar, fg_color="transparent")
        tools.pack(side="left", padx=(18, 0))
        self._bar_tools = tools

        def tool(icon, text, command):
            frame = ctk.CTkFrame(tools, corner_radius=8, border_width=math.ceil(1 / self._scale), height=34,
                                 fg_color=SURFACE, border_color=INK_400, cursor="hand2")
            frame.pack(side="left", padx=(8, 0))
            glyph = self._icon_label(frame, icon, 16, INK, cursor="hand2", height=20)
            glyph.pack(side="left", padx=(10, 6 if text else 10), pady=6)
            word = None
            if text is not None:
                word = self._label(frame, text, (FONT, 14, "bold"), INK, cursor="hand2", height=20)
                word.pack(side="left", padx=(0, 12), pady=6)
            for widget in (frame, glyph, word):
                if widget is not None:
                    widget.bind("<Button-1>", lambda _e: command())
            return frame, glyph, word

        self._sound_button = tool("speaker-high", "", self._toggle_sound_menu)
        tool("question", None, self._open_guide)
        self._refresh_sound_button()

    def _refresh_sound_button(self):
        """The speaker button: its icon and "70%" - or "Muted · 14 min"."""
        if self._sound_button is None:
            return
        _frame, glyph, word = self._sound_button
        sounds = self._sounds
        left = sounds.muted_seconds_left()
        if left:
            icon, text = "speaker-slash", f"Muted · {max(1, round(left / 60))} min"
        elif sounds.volume <= 0:
            icon, text = "speaker-slash", "Off"
        else:
            icon = "speaker-low" if sounds.volume < 0.5 else "speaker-high"
            text = f"{round(sounds.volume * 100)}%"
        glyph.configure(text=_icon(icon))
        word.configure(text=text)
        # While muted, the minutes count down - and it un-mutes by itself.
        if self._sound_tick_job is not None:
            self.window.after_cancel(self._sound_tick_job)
            self._sound_tick_job = None
        if left:
            self._sound_tick_job = self.window.after(min(20000, int(left * 1000) + 200), self._sound_tick)

    def _sound_tick(self):
        self._sound_tick_job = None
        if not self._closed:
            self._refresh_sound_button()
            if self._sound_menu is not None:
                self._sound_menu._render()

    def _toggle_sound_menu(self):
        import gate_tools

        if self._sound_menu is not None:
            self._sound_menu.close()
            return
        self._sound_menu = gate_tools.SoundMenu(self, self._sound_button[0], self._on_sound_change,
                                                self._sound_menu_closed)
        self._sound_menu_click = self.window.bind("<Button-1>", self._sound_menu_click_away, add="+")

    def _sound_menu_click_away(self, event):
        menu = self._sound_menu
        if menu is None:
            return
        widget = str(event.widget)
        if menu.owns(widget) or widget.startswith(str(self._sound_button[0])):
            return
        menu.close()

    def _sound_menu_closed(self):
        self._sound_menu = None
        try:
            self.window.unbind("<Button-1>", self._sound_menu_click)
        except tk.TclError:
            pass

    def _open_guide(self):
        import gate_tools

        if self._guide_window is not None:
            self._guide_window.lift()
            return
        self._guide_window = gate_tools.GuideWindow(self, lambda: setattr(self, "_guide_window", None))

    def _open_find_person(self):
        import gate_tools

        if self._find_window is not None:
            self._find_window.lift()
            return
        if not self._on_find_person:
            return
        self._find_window = gate_tools.FindPersonWindow(self, self._on_find_person, self._on_let_in,
                                                        lambda: setattr(self, "_find_window", None))

    def ask_exit_camera(self, camera_name, answer):
        """"A second camera is plugged in - use it as the exit camera?" -
        answer("use" | "later" | "never") on the Tk thread, or "skip" when it
        wasn't asked after all. Safe from any thread."""
        def ask():
            import gate_tools

            if self._closed or self._exit_camera_index is not None or self._setup_dialog is not None:
                answer("skip")  # meanwhile one was chosen, or the guard is choosing
                return
            gate_tools.ExitCameraQuestion(self, camera_name, answer)

        self.run_on_tk(ask)

    def set_camera_list(self, listed):
        """A fresh camera list from main.py (apply_camera_list) - Tk thread."""
        self._camera_list_ready(listed)

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
        self.stats_strip.grid(row=0, column=0, sticky="ew", pady=(0, s["gap"]))

        hero = ctk.CTkFrame(self.stats_strip, fg_color="transparent")
        hero.grid(row=0, column=0, sticky="sw", padx=(0, 34))
        self._hero_frame = hero
        self._label(hero, "TODAY", (COND_BOLD, s["stat_label"]), INK_600, height=18).pack(anchor="w")
        self.stat_tiles = {}
        self.stat_tiles["today"] = self._label(hero, "0", (WIDE_HEAVY, s["hero"]), INK, height=round(s["hero"] * 0.95))
        self.stat_tiles["today"].pack(anchor="w")

        self._stat_tile_frames = []
        for key, label, color, icon in self.STAT_SPECS:
            tile = ctk.CTkFrame(self.stats_strip, fg_color="transparent")
            # Plain tk.Frame - a CTkFrame this thin draws nothing at all.
            tk.Frame(tile, bg=LINE, width=max(1, self._px(1))).pack(side="left", fill="y")
            body = ctk.CTkFrame(tile, fg_color="transparent")
            body.pack(side="left", anchor="s", padx=(18, s["stat_pad"]))
            head = ctk.CTkFrame(body, fg_color="transparent")
            head.pack(anchor="w")
            self._icon_label(head, icon, s["stat_label"], color, height=18).pack(side="left", padx=(0, 5))
            self._label(head, label, (COND_BOLD, s["stat_label"]), color, height=18).pack(side="left")
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
        if self._closed or not getattr(self._view, "is_split", False) or self._left_column is None:
            return
        try:
            width = self._left_column.winfo_width()
        except tk.TclError:
            return  # the layout changed meanwhile
        if width <= 1:
            return
        # The hero's right padding isn't part of its requested width.
        needed = (self._hero_frame.winfo_reqwidth() + self._px(34)
                  + sum(t.winfo_reqwidth() for t in self._stat_tile_frames))
        wrapped = width < needed
        self._relayout_stats(self.STATS_COLUMNS_NARROW if wrapped else self.STATS_COLUMNS_WIDE)

    # ---- layouts ----------------------------------------------------------

    # The Layout menu (status bar): key -> (name, when to use it). The views
    # themselves are in layouts.py.
    LAYOUTS = {
        "split": ("Split", "Everyday default — camera and live log side by side."),
        "verify": ("Verify wall", "Checking faces — photo on file next to the live face, camera in a corner."),
        "full": ("Full camera", "Watching the crowd — video edge to edge, info floats on top."),
        "film": ("Filmstrip", "Wide monitors and TVs — camera on top, people slide by below."),
        "dual": ("Entrance + Exit", "Two-camera gates — both feeds, one merged log tagged IN / OUT."),
        "rail": ("Compact rail", "Small laptop screens — camera first, log as a rail of avatars."),
        "separate": ("Separate window", "Two monitors — the exit camera in its own window, with its own log."),
    }
    # What a layout saved before the Layout menu becomes: (layout, dual mode).
    OLD_LAYOUTS = {"side_by_side": ("split", "side"), "big_small": ("dual", "big"), "separate": ("separate", "side")}

    def _build_body(self):
        """Everything between the header and the status bar - the current
        layout's view (layouts.py), rebuilt when the guard picks another."""
        self._body = ctk.CTkFrame(self.window, fg_color="transparent", corner_radius=0)
        self._body.grid(row=2, column=0, sticky="nsew")
        self._feeds["entry"] = _Feed(self, self._body, "entry", **self._entry_feed_args)
        self._apply_layout()

    def _apply_layout(self):
        """(Re)builds the body for the current layout."""
        import layouts  # here, not at the top: layouts.py imports this module

        if self._view is not None:
            self._view.destroy()
            self._view = None
        for entry in self._log_entries:
            entry.pop("_row", None)  # the Split view's list rows went with it
        if self._layout != "separate" and self._exit_window is not None:
            self._exit_window.close(notify=False)
            self._exit_window = None
        if self._inset_big not in self._feeds:
            self._inset_big = "entry"
        self._view = layouts.VIEWS[self._layout](self, self._body)
        self._alert_key = self._alert_state_key()
        self._view.on_alert(self._alert)
        self._refresh_layout_button()

    def _attach_feed(self, direction, parent, background=CANVAS, **options):
        """Moves one camera's panel into a view's frame, drawn the way that
        layout wants it (_Feed.setup) - None if there's no such camera."""
        feed = self._feeds.get(direction)
        if feed is None:
            return None
        feed.reparent(parent, background)
        feed.setup(background=background, **options)
        return feed

    def _set_layout(self, layout):
        self._close_layout_menu()
        if layout not in self.LAYOUTS or layout == self._layout:
            return
        self._layout = layout
        self._verify_swap = None
        self._apply_layout()
        self._remember_layout()

    def _set_dual_mode(self, mode):
        """Entrance + Exit: side by side, or big + small."""
        if mode == self._dual_mode:
            return
        self._dual_mode = mode
        self._apply_layout()
        self._remember_layout()

    def _remember_layout(self):
        if self._on_layout_change:
            self._on_layout_change(self._layout, self._dual_mode)

    def _exit_window_closed(self):
        """The guard closed the exit camera's window - back to Split, with
        the exit camera as the small inset."""
        self._exit_window = None
        self._layout = "split"
        self._apply_layout()
        self._remember_layout()

    def _refresh_exit_counts(self):
        if self._exit_window is not None:
            self._exit_window.set_counts(self.stats["exits"], self.exit_stats["unknown"], self.exit_stats["spoof"])

    def show_exit_feed(self, get_preview_frame, get_camera_size=None, get_camera_fps=None, camera_zoom=1.0,
                       camera_index=None):
        """Adds the exit camera's feed to the current layout (Tk thread -
        main.py calls it from the camera choice, or at startup)."""
        self.hide_exit_feed()
        self._feeds["exit"] = _Feed(self, self._body, "exit", get_preview_frame, get_camera_size,
                                    get_camera_fps, camera_zoom, camera_index)
        self._exit_camera_index = camera_index
        self._apply_layout()
        self._render_mode_badge()

    def hide_exit_feed(self):
        feed = self._feeds.pop("exit", None)
        self._exit_camera_index = None
        if self._exit_window is not None:
            self._exit_window.close(notify=False)
            self._exit_window = None
        if feed is not None:
            feed.canvas.destroy()
            self._inset_big = "entry"
            if self._verify_swap == "exit":
                self._verify_swap = None
            self._apply_layout()
            self._refresh_in_frame()
            self._render_mode_badge()

    def _refresh_in_frame(self):
        """IN FRAME: faces in view across the cameras - "—" with none working."""
        working = [feed for feed in self._feeds.values() if not feed.showing_no_camera]
        self._in_frame_text = str(sum(len(feed.recognitions) for feed in working)) if working else "—"
        if self._view is not None:
            self._view.refresh_stats()

    def _stat_text(self, key):
        """A counter as shown. TODAY is everyone through the gate today -
        its entries and its exits."""
        if key == "in_frame":
            return self._in_frame_text
        if key == "today":
            return str(self.stats["entries"] + self.stats["exits"])
        return str(self.stats[key])

    def _alert_state_key(self):
        alert = self._alert
        return None if not alert else (alert["kind"], alert["time"], alert.get("direction"), alert.get("sub"))

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
        self._label(head, "Live log", (SEMI_BOLD, s["log_title"]), INK, anchor="w").grid(row=0, column=0, sticky="w")
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
        # + the details line (Live Log Details) and a card tap's note line.
        text_h = round(s["feat_name"] * 1.3 * 2 + 18 + 30 + 22)
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
        # STUDENT/STAFF tag + "school · department · role" (Settings > Live
        # Log Details) - only shown for a person who has any.
        self.feat_details = ctk.CTkFrame(feat_text, fg_color="transparent")
        self.feat_kind = self._label(self.feat_details, "", (COND_HEAVY, 14), "white", corner_radius=4, height=22)
        self.feat_detail_text = self._label(self.feat_details, "", (FONT, 15), INK_600, anchor="w")
        self.feat_id = self._label(feat_text, "", (FONT_MONO_MEDIUM, s["meta"]), INK_600, anchor="w")
        self.feat_id.pack(fill="x", anchor="w", pady=(6, 0))
        # What the card scanner strip used to say after a tap: NOT ENROLLED,
        # the person's distinguishing note, photos due for update.
        self.feat_note = self._label(feat_text, "", (FONT, s["dept"], "bold"), CAUTION, anchor="w", justify="left")
        self.feat_match = ctk.CTkFrame(self.feat_body, fg_color="transparent")
        self.feat_match.grid(row=0, column=2, sticky="e", padx=(12, 18))
        self._label(self.feat_match, "MATCH", (COND_BOLD, 14), INK_600, anchor="e").pack(anchor="e")
        self.feat_conf = self._label(self.feat_match, "", (FONT_MONO_SEMIBOLD, s["feat_conf"]), INK, anchor="e")
        self.feat_conf.pack(anchor="e")

        self.feat_empty = self._label(
            log, "Waiting for the first scan or tap…", (FONT, 16), INK_600, anchor="w"
        )

        self._label(log, "EARLIER", (COND_BOLD, 13), INK_600, anchor="w").grid(
            row=3, column=0, sticky="w", pady=(s["gap"], 6)
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
        """The list's width changed: re-fit the texts that were shortened to
        it - only re-setting their text, never recreating the rows (that
        used to rebuild the whole list a second time after every event,
        since adding a row nudges the width)."""
        self._log_relayout_job = None
        if self._closed or not getattr(self._view, "is_split", False):
            return  # another layout now - its list is gone
        if self._log_entries:
            self._render_featured(self._log_entries[0])
        row_font, meta_font = self._log_fonts()
        for entry in self._log_entries[1:]:
            row = entry.get("_row")
            if row is None:
                continue
            width = self._log_name_width(row["word_w"])
            row["name"].configure(text=_ellipsize(row_font, entry["name"], width))
            row["meta"].configure(text=_ellipsize(meta_font, self._meta(entry), width))

    def _build_status_bar(self):
        tk.Frame(self.window, bg=LINE, height=max(1, self._px(1))).grid(row=3, column=0, sticky="ew")
        bar = ctk.CTkFrame(self.window, fg_color=SURFACE, corner_radius=0, height=self._s["foot_h"])
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
        self._build_setup_button(inner)
        self._build_student_toggle(inner)
        self._build_layout_button(inner)
        self._build_find_button(inner)

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
        # Sound and quick guide - always right after the guard's name or sign-in
        # (_render_gate_sign_in packs those before them).
        self._build_bar_tools(inner)

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

    def _build_setup_button(self, bar):
        """"Cameras & card readers" - opens the window where the guard picks
        the entrance camera, the exit camera (or none - an entrance-only
        gate), and which card reader is the exit's (_open_setup_dialog). A
        laptop with more than one camera has no other way to tell the gate
        monitor which one to use."""
        button = ctk.CTkFrame(bar, corner_radius=8, border_width=math.ceil(1 / self._scale), height=34,
                              fg_color=SURFACE, border_color=MAROON, cursor="hand2")
        button.pack(side="left", padx=(12, 0))
        icon = self._icon_label(button, "video-camera", 16, MAROON, height=20)
        icon.pack(side="left", padx=(12, 8), pady=6)
        label = self._label(button, "Cameras & card readers", (FONT, 14, "bold"), MAROON, height=20)
        label.pack(side="left", padx=(0, 12), pady=6)
        for widget in (button, icon, label):
            widget.bind("<Button-1>", lambda _e: self._open_setup_dialog())
            widget.configure(cursor="hand2")

    def _build_find_button(self, bar):
        """"Find person" - look someone up, let them in by hand (gate_tools)."""
        button = ctk.CTkFrame(bar, corner_radius=8, border_width=math.ceil(1 / self._scale), height=34,
                              fg_color=SURFACE, border_color=INK_400, cursor="hand2")
        button.pack(side="left", padx=(12, 0))
        icon = self._icon_label(button, "magnifying-glass", 16, INK, height=20)
        icon.pack(side="left", padx=(12, 8), pady=6)
        label = self._label(button, "Find person", (FONT, 14, "bold"), INK, height=20)
        label.pack(side="left", padx=(0, 12), pady=6)
        for widget in (button, icon, label):
            widget.bind("<Button-1>", lambda _e: self._open_find_person())
            widget.configure(cursor="hand2")

    # Values are "index: label" strings (e.g. "0: Logitech BRIO") so the
    # index survives CTkOptionMenu's plain-string API.
    NO_EXIT_CAMERA = "None - this gate records entries only"

    def _camera_values(self):
        return {f"{index}: {label}": index for index, label in self._camera_options}

    def _open_setup_dialog(self):
        """The "Cameras & card readers" window: the entrance camera, the exit
        camera, and the exit card reader. Changes take effect at once and are
        remembered for next time (main.py)."""
        if self._setup_dialog is not None:
            self._setup_dialog.lift()
            return
        self._refresh_camera_list()
        dialog = ctk.CTkToplevel(self.window)
        dialog.title("Cameras & card readers")
        dialog.configure(fg_color=SURFACE)
        dialog.resizable(False, False)
        dialog.transient(self.window)
        dialog.protocol("WM_DELETE_WINDOW", self._close_setup_dialog)
        # The keyboard stays with this window while it's open (see
        # _keep_focus), so a card tap typed here - "Set exit reader", or an
        # ordinary tap - is passed on to the card field, as the student display does.
        dialog.bind("<Key>", self._forward_card_key)
        body = ctk.CTkFrame(dialog, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=28, pady=24)
        self._label(body, "Cameras & card readers", (SEMI_BOLD, 22), INK, anchor="w").pack(fill="x")
        self._label(body, f"{self.gate_location} - one camera and one card reader for each side of the gate.",
                    (FONT, 14), INK_600, anchor="w", justify="left", wraplength=470).pack(fill="x", pady=(4, 14))

        values = self._camera_values()
        names = list(values) or ["No camera found"]
        menu_style = dict(width=470, height=34, corner_radius=3, font=(FONT_MONO, 13), dropdown_font=(FONT_MONO, 13),
                          fg_color=CANVAS, text_color=INK, button_color=CANVAS, button_hover_color=LINE,
                          dropdown_fg_color=SURFACE, dropdown_hover_color=CANVAS, dropdown_text_color=INK)

        def current(index):
            return next((name for name, value in values.items() if value == index), None)

        self._label(body, "ENTRANCE CAMERA", (COND_BOLD, 14), INK_600, anchor="w").pack(fill="x")
        entry_menu = ctk.CTkOptionMenu(body, values=names, command=self._entry_camera_picked, **menu_style)
        entry_menu.set(current(self._feeds["entry"].camera_index) or names[0])
        entry_menu.pack(fill="x", pady=(4, 12))
        self._label(body, "EXIT CAMERA", (COND_BOLD, 14), INK_600, anchor="w").pack(fill="x")
        exit_menu = ctk.CTkOptionMenu(body, values=[self.NO_EXIT_CAMERA, *values], command=self._exit_camera_picked,
                                      **menu_style)
        exit_menu.set(current(self._exit_camera_index) or self.NO_EXIT_CAMERA)
        exit_menu.pack(fill="x", pady=(4, 4))
        if not values:
            entry_menu.configure(state="disabled")
            exit_menu.configure(state="disabled")
        camera_note = self._label(body, "", (FONT, 13, "bold"), DANGER, anchor="w", justify="left", wraplength=470)
        camera_note.pack(fill="x", pady=(0, 4))
        # How the cameras are shown is the Layout menu in the status bar.
        self._label(body, "How they're shown on screen: the Layout button in the status bar.", (FONT, 13),
                    INK_600, anchor="w", justify="left", wraplength=470).pack(fill="x", pady=(0, 10))

        tk.Frame(body, bg=LINE, height=max(1, self._px(1))).pack(fill="x", pady=(4, 14))
        self._label(body, "EXIT CARD READER", (COND_BOLD, 14), INK_600, anchor="w").pack(fill="x")
        reader_text = self._label(body, "", (FONT, 14), INK, anchor="w", justify="left", wraplength=470)
        reader_text.pack(fill="x", pady=(4, 10))
        buttons = ctk.CTkFrame(body, fg_color="transparent")
        buttons.pack(fill="x")
        button_style = dict(font=(FONT, 14, "bold"), height=38, corner_radius=8)
        set_button = ctk.CTkButton(buttons, text="Set exit reader", fg_color=MAROON, hover_color=MAROON_DEEP,
                                   text_color="white", command=lambda: self._reader_action("set_exit"), **button_style)
        set_button.pack(side="left")
        clear_button = ctk.CTkButton(
            buttons, text="Clear", fg_color=SURFACE, hover_color=CANVAS, text_color=INK,
            border_width=math.ceil(1 / self._scale), border_color=INK_400,
            command=lambda: self._reader_action("clear"), **button_style,
        )
        clear_button.pack(side="left", padx=(10, 0))
        ctk.CTkButton(
            body, text="Close", fg_color=SURFACE, hover_color=CANVAS, text_color=INK,
            border_width=math.ceil(1 / self._scale), border_color=INK_400, command=self._close_setup_dialog,
            **button_style,
        ).pack(anchor="e", pady=(22, 0))

        self._setup_dialog = dialog
        self._setup_widgets = {"entry": entry_menu, "exit": exit_menu, "camera_note": camera_note,
                               "reader_text": reader_text, "set": set_button, "clear": clear_button}
        self._render_reader_status()
        dialog.update_idletasks()
        x = self.window.winfo_rootx() + (self.window.winfo_width() - dialog.winfo_reqwidth()) // 2
        y = self.window.winfo_rooty() + (self.window.winfo_height() - dialog.winfo_reqheight()) // 3
        dialog.geometry(f"+{max(0, x)}+{max(0, y)}")
        dialog.after(150, dialog.lift)

    def _refresh_camera_list(self):
        """Asks main.py for the cameras as they are now (refresh_cameras) -
        listed off this thread, so the window opens at once; the lists fill
        in when the answer comes (_camera_list_ready)."""
        if not self._on_refresh_cameras:
            return
        try:
            self._on_refresh_cameras(self._camera_list_ready)
        except Exception:
            traceback.print_exc()

    def _camera_list_ready(self, fresh):
        """The fresh camera list (Tk thread): a camera plugged in since the
        gate monitor opened shows up, and the ones in use keep their right
        numbers. None: listing failed - the last list stays."""
        if not fresh or self._closed:
            return
        options, entry_index, exit_index = fresh
        self._camera_options = options
        self._feeds["entry"].camera_index = entry_index
        self._exit_camera_index = exit_index
        if "exit" in self._feeds:
            self._feeds["exit"].camera_index = exit_index
        if self._setup_dialog is not None:
            self._fill_camera_menus()

    def _fill_camera_menus(self):
        """The two camera lists in the open Cameras & card readers window,
        from _camera_options - each showing the camera in use."""
        values = self._camera_values()
        names = list(values) or ["No camera found"]

        def current(index):
            return next((name for name, value in values.items() if value == index), None)

        widgets = self._setup_widgets
        state = "normal" if values else "disabled"
        widgets["entry"].configure(values=names, state=state)
        widgets["entry"].set(current(self._feeds["entry"].camera_index) or names[0])
        widgets["exit"].configure(values=[self.NO_EXIT_CAMERA, *values], state=state)
        widgets["exit"].set(current(self._exit_camera_index) or self.NO_EXIT_CAMERA)

    def run_on_tk(self, fn):
        """Runs fn on the Tk thread, from any thread (through the queue)."""
        self._queue.put(("call", fn))

    def _close_setup_dialog(self):
        if self._readers.get("waiting") and self._on_reader_setup:
            self._on_reader_setup("cancel")
        if self._setup_dialog is not None:
            self._setup_dialog.destroy()
            self._setup_dialog = None

    def _entry_camera_picked(self, value):
        index = self._camera_values().get(value)
        widgets = self._setup_widgets
        if index is None:
            return
        if index == self._exit_camera_index:
            widgets["camera_note"].configure(text="That camera is the exit camera - each side needs its own.")
            widgets["entry"].set(next((name for name, i in self._camera_values().items()
                                       if i == self._feeds["entry"].camera_index), value))
            return
        widgets["camera_note"].configure(text="")
        self._feeds["entry"].camera_index = index
        if self._on_camera_change:
            self._on_camera_change(index)

    def _exit_camera_picked(self, value):
        index = self._camera_values().get(value)  # None: "no exit camera"
        widgets = self._setup_widgets
        if index is not None and index == self._feeds["entry"].camera_index:
            widgets["camera_note"].configure(text="That camera is the entrance camera - each side needs its own.")
            widgets["exit"].set(next((name for name, i in self._camera_values().items()
                                      if i == self._exit_camera_index), self.NO_EXIT_CAMERA))
            return
        widgets["camera_note"].configure(text="")
        if self._on_exit_camera_change:
            self._on_exit_camera_change(index)

    def _reader_action(self, action):
        if self._on_reader_setup:
            self._on_reader_setup(action)

    def set_reader_status(self, status):
        """status from card_readers.CardReaders.status(), plus an optional
        "message" - the exit reader as set, or "waiting for a tap"."""
        self._queue.put(("reader_status", status))

    def _render_reader_status(self):
        status = self._readers
        exit_reader = status.get("exit_reader")
        if self._setup_dialog is None:
            return
        widgets = self._setup_widgets
        if not status.get("available"):
            text, color = ("This computer can't tell card readers apart, so every tap counts as the entrance's.",
                           CAUTION)
        elif status.get("waiting"):
            text, color = "Tap any card on the reader at the EXIT now…", PROMPT
        elif exit_reader:
            text, color = (f"Set: {exit_reader}. Every other reader is the entrance's. Keep each reader in its "
                           "USB port - they're told apart by port.", INK)
        else:
            text, color = "Not set - every reader counts as the entrance's.", INK_600
        if status.get("message"):
            text = f"{status['message']} {text}"
        widgets["reader_text"].configure(text=text, text_color=color)
        widgets["set"].configure(state="normal" if status.get("available") and not status.get("waiting")
                                 else "disabled")
        widgets["clear"].configure(state="normal" if exit_reader or status.get("waiting") else "disabled")

    def _build_layout_button(self, bar):
        """"Layout: Split ^" - opens the Layout menu (_open_layout_menu)."""
        button = ctk.CTkFrame(bar, corner_radius=8, border_width=math.ceil(1 / self._scale), height=34,
                              fg_color=SURFACE, border_color=INK_400, cursor="hand2")
        button.pack(side="left", padx=(12, 0))
        self._layout_icon = self._icon_label(button, "layout", 16, INK, height=20, cursor="hand2")
        self._layout_icon.pack(side="left", padx=(12, 8), pady=6)
        self._layout_word = self._label(button, "", (FONT, 14, "bold"), INK, height=20, cursor="hand2")
        self._layout_word.pack(side="left", pady=6)
        self._layout_caret = self._icon_label(button, "caret-up", 12, INK, height=20, cursor="hand2")
        self._layout_caret.pack(side="left", padx=(8, 12), pady=6)
        for widget in (button, self._layout_icon, self._layout_word, self._layout_caret):
            widget.bind("<Button-1>", lambda _e: self._toggle_layout_menu())
        self._layout_button = button
        self._refresh_layout_button()

    def _refresh_layout_button(self):
        if getattr(self, "_layout_button", None) is None:
            return  # the status bar is built after the first layout
        is_open = self._layout_menu is not None
        bg, fg = (INK, SURFACE) if is_open else (SURFACE, INK)
        self._layout_button.configure(fg_color=bg, border_color=INK if is_open else INK_400)
        for widget in (self._layout_icon, self._layout_word, self._layout_caret):
            widget.configure(text_color=fg, fg_color=bg)
        self._layout_word.configure(text=f"Layout: {self.LAYOUTS[self._layout][0]}")

    def _toggle_layout_menu(self):
        if self._layout_menu is not None:
            self._close_layout_menu()
        else:
            self._open_layout_menu()

    def _open_layout_menu(self):
        """The Layout menu, popping up above its button: each layout with a
        little picture of its shape, what it's for, and a check on the
        current one. Picking one switches at once and is remembered."""
        import layouts

        menu = ctk.CTkFrame(self.window, fg_color=SURFACE, corner_radius=10, border_width=1, border_color=INK_400)
        self._label(menu, "SCREEN LAYOUT", (COND_BOLD, 12), INK_600, anchor="w").pack(fill="x", padx=18,
                                                                                  pady=(12, 4))
        thumb_w, thumb_h = 92, 58
        for key, (name, description) in self.LAYOUTS.items():
            current = key == self._layout
            bg = CANVAS if current else SURFACE
            item = ctk.CTkFrame(menu, fg_color=bg, corner_radius=6, cursor="hand2")
            item.pack(fill="x", padx=10, pady=1)
            picture = layouts.layout_thumb(key, self._px(thumb_w), self._px(thumb_h))
            thumb = ctk.CTkLabel(item, text="", image=self._layout_thumb_image(key, picture, thumb_w, thumb_h),
                                 fg_color=bg)
            thumb.pack(side="left", padx=8, pady=8)
            check = self._icon_label(item, "check", 18, MAROON if current else bg, fg_color=bg)
            check.pack(side="right", padx=(8, 12))
            text = ctk.CTkFrame(item, fg_color=bg, corner_radius=0)
            text.pack(side="left", fill="x", expand=True, padx=(6, 0))
            self._label(text, name, (FONT, 15, "bold"), INK, anchor="w", fg_color=bg).pack(fill="x")
            self._label(text, description, (FONT, 13), INK_600, anchor="w", justify="left", wraplength=320,
                        fg_color=bg).pack(fill="x")
            for widget in (item, thumb, check, text, *text.winfo_children()):
                widget.bind("<Button-1>", lambda _e, chosen=key: self._set_layout(chosen))
        ctk.CTkFrame(menu, fg_color="transparent", height=8).pack()
        self._layout_menu = menu
        self.window.update_idletasks()
        x = self._layout_button.winfo_rootx() - self.window.winfo_rootx()
        y = self._layout_button.winfo_rooty() - self.window.winfo_rooty() - self._px(10)
        menu.place(x=x, y=y, anchor="sw")
        menu.lift()
        # A click anywhere else closes it.
        self._layout_menu_click = self.window.bind("<Button-1>", self._layout_menu_click_away, add="+")
        self._refresh_layout_button()

    def _layout_thumb_image(self, key, picture, width, height):
        image = ctk.CTkImage(light_image=picture, dark_image=picture, size=(width, height))
        self._card_images[f"layout_{key}"] = image  # keep a reference or Tk drops it
        return image

    def _layout_menu_click_away(self, event):
        menu = self._layout_menu
        if menu is None:
            return
        widget = str(event.widget)
        if widget.startswith(str(menu)) or widget.startswith(str(self._layout_button)):
            return
        self._close_layout_menu()

    def _close_layout_menu(self):
        if self._layout_menu is None:
            return
        try:
            self.window.unbind("<Button-1>", self._layout_menu_click)
        except tk.TclError:
            pass
        self._layout_menu.destroy()
        self._layout_menu = None
        self._refresh_layout_button()

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

        # The student display faces the entrance - it shows the entrance
        # camera only, whether or not the gate has an exit camera.
        entrance = self._feeds["entry"]
        self._student = StudentDisplayWindow(
            self.window, self.gate_location, self.direction, entrance.get_preview_frame,
            is_camera_down=lambda: entrance.showing_no_camera,
            on_card_key=self._forward_card_key,
            on_close=self._student_closed,
            fps=self._student_display_fps,
        )
        self._student.update_recognitions(entrance.recognitions, entrance.image_size)
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

    def show_recognitions(self, recognitions, image_size, direction="entry"):
        """recognitions: list of dicts with box/matched/name/student_id/
        department/confidence/log_id/deduped, one per face in the latest
        scanned frame of the `direction` camera (entrance or exit).
        image_size: (width, height) of that frame, so boxes (in that frame's
        pixel coordinates) can be scaled onto the preview."""
        self._queue.put(("recognitions", (recognitions, image_size, direction)))

    def show_offline(self, offline):
        self._queue.put(("offline", offline))

    def seed_stats(self, entries_today, unknown_today, spoof_today=0, occlusion_today=0, exits_today=0):
        self._queue.put(("seed", (entries_today, unknown_today, spoof_today, occlusion_today, exits_today)))

    def set_threshold(self, threshold):
        self._queue.put(("threshold", threshold))

    def set_video_fps(self, fps):
        """Settings > Camera's "Gate monitor video smoothness" (or this
        computer's speed mode, on Auto) - from the next redraw."""
        self._queue.put(("video_fps", fps))

    def set_student_display_fps(self, fps):
        """Settings > Camera's "Student display smoothness"."""
        self._queue.put(("student_display_fps", fps))

    def set_after_hours(self, after_hours):
        """Settings > Scheduling > Gate opening hours: whether it's outside
        them right now (the backend sends it with every scan answer)."""
        self._queue.put(("after_hours", bool(after_hours)))

    def set_test_mode(self):
        """The capstone test mode: the header pill says TEST MODE in red for
        as long as this gate monitor is open, so nobody mistakes it for the
        real gate. Call on the Tk thread, before run()."""
        self._test_mode = True
        self.window.title(f"{self.window.title()} - TEST MODE")
        self._mode_badge.configure(fg_color=DANGER)
        self._mode_icon.configure(text=_icon("warning"), text_color="white", fg_color=DANGER)
        self._mode_word.configure(text="TEST MODE", text_color="white", fg_color=DANGER)

    def set_alert_settings(self, alerts):
        """alerts: from the backend (Settings > Alerts) - "spoof": whether a
        suspected fake gets the alarm banner and sound; "unknown_sound",
        "covered_sound", "not_enrolled_sound": whether those play their sound."""
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
        """A guard's staff ID card tap (or face scan) signed them in."""
        self._queue.put(("staff_signed_in", name))

    def ask_for_face(self, name, pending):
        """"Card, then face": the staff card tap was the first step - the
        sign-in window now asks its owner to look at the camera."""
        self._queue.put(("face_step", (name, pending)))

    def face_in_view(self):
        """Whether the entrance camera's latest scan answer had a face in it
        - safe from any thread (it only reads). The sign-in window's face scan
        (main.face_sign_in_loop) asks before sending a frame."""
        feed = self._feeds.get("entry")
        return bool(feed is not None and feed.recognitions)

    def show_face_sign_in(self, result):
        """One face-scan answer from the backend ({"reason", "reason_code",
        "name"?}) - shown under the sign-in window's camera view."""
        self._queue.put(("face_sign_in", result))

    def current_shift_id(self):
        """The shift on duty now (for signing out when the window closes) -
        read from the Tk thread's last status, so it's safe from any thread."""
        on_duty = self._sign_in.get("on_duty") or {}
        return on_duty.get("shift_id")

    def set_backend_ok(self, ok, bad_text="Backend not OK"):
        self._queue.put(("backend_ok", (ok, bad_text)))

    def set_camera_ok(self, ok, bad_text="Camera not connected"):
        self._queue.put(("camera_ok", (ok, bad_text)))

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
            recognitions, image_size, direction = payload
            self._render_recognitions(recognitions, image_size, direction)
        elif kind == "offline":
            self._render_offline(payload)
        elif kind == "seed":
            entries, unknown, spoof, occlusion, exits = payload
            self.stats["entries"], self.stats["exits"] = entries, exits
            self.stats["unknown"], self.stats["spoof"] = unknown, spoof
            self.stats["occlusion"] = occlusion
            self._refresh_stat_labels()
        elif kind == "reader_status":
            self._readers = payload
            self._render_reader_status()
        elif kind == "threshold":
            self.threshold_label.configure(text=f"{payload:.2f}")
        elif kind == "after_hours":
            self._render_after_hours(payload)
        elif kind == "video_fps":
            self._video_refresh_ms = round(1000 / payload) if payload else VIDEO_REFRESH_MS
        elif kind == "student_display_fps":
            self._student_display_fps = payload
            if self._student is not None:
                self._student.set_fps(payload)
        elif kind == "alert_settings":
            self._alert_spoof = bool(payload.get("spoof", True))
            for sound in self._sound_on:
                self._sound_on[sound] = bool(payload.get(f"{sound}_sound", True))
        elif kind == "card_match":
            self._render_card_match(payload)
        elif kind == "card_failure":
            reason, reason_code = payload
            self._render_card_failure(reason, reason_code)
        elif kind == "card_status":
            self._set_tap("Checking card…", "circle-notch", INK_600)
        elif kind == "queue_count":
            self.queue_label.configure(
                text=f"Queue {payload}", text_color=CAUTION if payload else INK_600
            )
        elif kind == "backend_ok":
            ok, bad_text = payload if isinstance(payload, tuple) else (payload, "Backend not OK")
            self._set_status(self.backend_icon, self.backend_label, ok,
                             "Backend OK", bad_text, DANGER, "x-circle")
        elif kind == "camera_ok":
            ok, bad_text = payload if isinstance(payload, tuple) else (payload, "Camera not connected")
            self._set_status(self.camera_icon, self.camera_label, ok,
                             "Cameras OK" if "exit" in self._feeds else "Camera OK", bad_text, CAUTION, "warning")
        elif kind == "gate_sign_in":
            self._render_gate_sign_in(payload)
        elif kind == "sign_in_result":
            ok, message = payload
            self._render_sign_in_result(ok, message)
        elif kind == "staff_signed_in":
            self._render_staff_signed_in(payload)
        elif kind == "face_step":
            self._render_face_step(*payload)
        elif kind == "face_sign_in":
            self._render_face_sign_in(payload)
        elif kind == "call":
            payload()

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
        if self._find_window is not None:
            self._find_window.refresh_duty()  # who a manual entry is recorded under
        if not self._sign_in.get("enabled") or self._sign_in.get("on_duty"):
            self._cancel_sign_in_reminder()
        if not self._sign_in.get("enabled"):
            self._duty.pack_forget()
            if self._identity_label is not None and not self._identity_label.winfo_manager():
                self._identity_label.pack(side="left", padx=(26, 0), before=self._bar_tools)
            self._close_sign_in_dialog()
            return
        if self._identity_label is not None:
            self._identity_label.pack_forget()
        if not self._duty.winfo_manager():
            self._duty.pack(side="left", padx=(26, 0), before=self._bar_tools)
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
                # The ways to sign in changed on the Settings page while the
                # window was open: rebuild it with the new ones.
                if self._sign_in_dialog is not None and self._dialog_ways != self._sign_in_ways():
                    self._close_sign_in_dialog()
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

    # How a guard may sign in (Settings > Security & Accounts) - what an older
    # backend, which doesn't say, always allowed.
    DEFAULT_SIGN_IN_WAYS = {"password": True, "card": True, "face": False, "card_then_face": False}
    FACE_VIEW_SIZE = (380, 214)  # logical px, 16:9
    FACE_PREVIEW_MS = 125  # 8 a second - enough to aim a face, light on the CPU

    def _sign_in_ways(self):
        return {**self.DEFAULT_SIGN_IN_WAYS, **(self._sign_in.get("methods") or {})}

    def _sign_in_intro(self, ways):
        """"Nobody is on duty at Main Gate. Tap your staff ID card on the
        reader, then look at the camera - or type your username and password.\""""
        if ways["card_then_face"]:
            steps = "Tap your staff ID card on the reader, then look at the camera"
            return (f"Nobody is on duty at {self.gate_location}. {steps}"
                    + (" - or type your username and password." if ways["password"] else "."))
        parts = [text for key, text in (("card", "tap your staff ID card on the reader"),
                                        ("face", "look at the camera"),
                                        ("password", "type your username and password")) if ways[key]]
        joined = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " or " + parts[-1]
        return f"Nobody is on duty at {self.gate_location}. {joined[0].upper()}{joined[1:]}."

    def _open_sign_in_dialog(self):
        if self._sign_in_dialog is not None:
            self._sign_in_dialog.lift()
            return
        ways = self._sign_in_ways()
        self._dialog_ways = ways
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
        self._label(body, self._sign_in_intro(ways),
                    (FONT, 14), INK_600, anchor="w", justify="left", wraplength=380).pack(fill="x", pady=(6, 4))
        self._label(body, "The gate keeps scanning meanwhile - its entries are marked Unattended until a guard "
                          "signs in.",
                    (FONT, 13), INK_600, anchor="w", justify="left", wraplength=380).pack(fill="x", pady=(0, 16))
        self._sign_in_fields = {}
        self._sign_in_submit = None
        self._face_view = None
        if ways["face"]:
            self._build_face_section(body, ways)
        if ways["password"] and ways["face"]:
            self._label(body, "Or with your password", (FONT, 13, "bold"), INK_600, anchor="w").pack(
                fill="x", pady=(4, 8))
        for key, text, secret in ((("username", "Username", False), ("password", "Password", True))
                                  if ways["password"] else ()):
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
        if ways["password"]:
            self._sign_in_submit = ctk.CTkButton(
                buttons, text="Sign in", font=(FONT, 14, "bold"), height=42, corner_radius=8, fg_color=MAROON,
                hover_color=MAROON_DEEP, text_color="white", command=self._submit_sign_in,
            )
            self._sign_in_submit.pack(side="right")
        else:
            # No text boxes to catch a card tap (see _KeyBurst) - the window
            # itself passes the reader's typing on.
            dialog.bind("<Key>", self._forward_card_key)
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
        first = self._sign_in_fields.get("username") or dialog
        dialog.after(150, lambda: (dialog.lift(), first.focus_force()))
        if ways["face"]:
            if not ways["card_then_face"] or self._face_step:
                self._start_face_scan()
            self._face_preview_tick()

    def _build_face_section(self, body, ways):
        """The entrance camera's live view with an oval to put the face in,
        and a line saying how the face scan is going."""
        width, height = (self._px(side) for side in self.FACE_VIEW_SIZE)
        holder = ctk.CTkFrame(body, fg_color=INK, corner_radius=6)
        holder.pack(fill="x", pady=(0, 8))
        self._face_view = tk.Canvas(holder, width=width, height=height, bg="#1c1c1c", highlightthickness=0, bd=0)
        self._face_view.pack(padx=self._px(4), pady=self._px(4))
        if ways["card_then_face"] and not self._face_step:
            first = "Tap your staff ID card first."
        elif self._face_step:
            first = f"{self._face_step['name']}: now look at the camera."
        else:
            first = "Look at the camera."
        self._face_status = self._label(body, first, (FONT, 14, "bold"), INK_600, anchor="w", justify="left",
                                        wraplength=380)
        self._face_status.pack(fill="x", pady=(0, 12))

    def _face_preview_tick(self):
        self._face_preview_job = None
        canvas = self._face_view
        if self._closed or self._sign_in_dialog is None or canvas is None:
            return
        width, height = (self._px(side) for side in self.FACE_VIEW_SIZE)
        feed = self._feeds.get("entry")
        frame = feed.get_preview_frame() if (feed is not None and self._face_scanning) else None
        canvas.delete("all")
        if frame is not None:
            import cv2  # loaded by the camera already (see _Feed)

            # Cropped to the view's shape and shrunk with OpenCV, then pasted
            # into the same Tk image - cheap, since the face scan is running
            # on this computer at the same time.
            src_h, src_w = frame.shape[:2]
            scale = max(width / src_w, height / src_h)
            crop_w, crop_h = min(src_w, round(width / scale)), min(src_h, round(height / scale))
            x0, y0 = (src_w - crop_w) // 2, (src_h - crop_h) // 2
            small = cv2.resize(frame[y0:y0 + crop_h, x0:x0 + crop_w], (width, height), interpolation=cv2.INTER_AREA)
            picture = Image.fromarray(cv2.cvtColor(small, cv2.COLOR_BGR2RGB))
            if self._face_photo is not None and (self._face_photo.width(), self._face_photo.height()) == (width, height):
                self._face_photo.paste(picture)
            else:
                self._face_photo = ImageTk.PhotoImage(picture)
            canvas.create_image(0, 0, image=self._face_photo, anchor="nw")
            oval_w, oval_h = width * 0.30, height * 0.78
            canvas.create_oval(width / 2 - oval_w / 2, height / 2 - oval_h / 2, width / 2 + oval_w / 2,
                               height / 2 + oval_h / 2, outline="white", width=max(1, self._px(2)), dash=(6, 4))
        else:
            words = "Waiting for the camera…" if self._face_scanning else "Tap your staff ID card"
            canvas.create_text(width // 2, height // 2, text=words, fill="#d0d0d0",
                               font=self._tkfont(FONT, 15, "bold"))
        self._face_preview_job = self.window.after(self.FACE_PREVIEW_MS, self._face_preview_tick)

    def _start_face_scan(self):
        self._stop_face_scan()
        self._face_scanning = True
        if self._on_face_scan:
            self._on_face_scan(True, (self._face_step or {}).get("pending"))

    def _stop_face_scan(self):
        if self._face_scanning and self._on_face_scan:
            self._on_face_scan(False, None)
        self._face_scanning = False

    def _render_face_step(self, name, pending):
        """A staff card tap with "Card, then face": open (or switch) the
        sign-in window to its face step for that card's owner."""
        self._cancel_sign_in_reminder()
        # The card was the first step - the pill says what comes next (it
        # would otherwise still say "Checking card…").
        self._set_tap(f"{name} · now look at the camera", "user-focus", PROMPT)
        ways = self._sign_in_ways()
        if not ways["card_then_face"]:  # the backend says so - this gate's copy was behind
            self._sign_in = {**self._sign_in, "methods": {**ways, "card": True, "face": True,
                                                          "card_then_face": True}}
        if self._sign_in_dialog is not None and self._face_view is not None:
            self._face_step = {"name": name, "pending": pending}
            self._face_status.configure(text=f"{name}: now look at the camera.", text_color=INK)
            self._start_face_scan()
            self._sign_in_dialog.lift()
            return
        self._close_sign_in_dialog()
        self._face_step = {"name": name, "pending": pending}
        self._open_sign_in_dialog()

    # How each face-scan answer reads under the camera view.
    FACE_STATUS_COLORS = {"checking": VERIFIED, "no_face": INK_600, "no_camera": INK_600, "offline": CAUTION,
                          "server_error": CAUTION}

    def _render_face_sign_in(self, result):
        if self._sign_in_dialog is None or self._face_view is None:
            return
        code = result.get("reason_code")
        text = result.get("reason") or "Look at the camera."
        if code == "checking" and result.get("name"):
            text = f"{result['name']} - hold still…"
        if code in ("card_first", "face_off"):
            # The card step ran out (or face sign-in was just switched off):
            # main.py has stopped sending frames.
            self._face_scanning = False
            if code == "card_first" and self._face_step:
                text = "That took too long - tap your staff ID card again."
            self._face_step = None
        self._face_status.configure(text=text, text_color=self.FACE_STATUS_COLORS.get(code, DANGER))

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
            other = " - or tap your staff ID card." if self._sign_in_ways()["card"] else "."
            self._sign_in_error.configure(text=f"Type your username and password{other}")
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
        if self._sign_in_submit is not None:
            self._sign_in_submit.configure(state="normal", text="Sign in")
        if "password" in self._sign_in_fields:
            self._sign_in_fields["password"].delete(0, "end")
        self._sign_in_error.configure(text=message or "Couldn't sign in.")

    def _close_sign_in_dialog(self):
        self._stop_face_scan()
        self._face_step = None
        if self._face_preview_job is not None:
            self.window.after_cancel(self._face_preview_job)
            self._face_preview_job = None
        self._face_view = None
        if self._sign_in_dialog is not None:
            self._sign_in_dialog.destroy()
            self._sign_in_dialog = None

    def _render_staff_signed_in(self, name):
        """A staff ID card tap (or face scan) signed its owner in - the card
        pill on the camera says so for a moment."""
        self._set_tap(f"On duty · {name}", "user-focus", VERIFIED)
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
        # Same for the exit camera's own window (it forwards taps too) -
        # pulling focus back would raise the gate monitor over it.
        student_has_focus = (self._student is not None and self._student.owns(focused)) or (
            self._exit_window is not None and self._exit_window.owns(focused))
        # The sign-in window takes typing too - and catches a card tap
        # itself (see _KeyBurst).
        dialog_has_focus = (self._sign_in_dialog is not None and focused is not None
                            and str(focused).startswith(str(self._sign_in_dialog)))
        # Never while the "Cameras & card readers" window is open: taking the
        # keyboard back closed its drop-down lists the moment they opened (an
        # open list's focus even reads as "none"). It forwards taps instead.
        setup_open = self._setup_dialog is not None
        if (focused is not self._card_input and not student_has_focus and not dialog_has_focus and not setup_open
                and not self._typing_elsewhere(focused)):
            self._card_input.focus_force()
        self.window.after(FOCUS_CHECK_MS, self._keep_focus)

    def allow_typing_in(self, toplevel):
        """Lets someone type into the text boxes of another window of ours
        (the test mode's window) - otherwise the card-reader field takes the
        keyboard back within a moment. Only while a visible text box there
        has the keyboard; anywhere else it still goes back to the reader."""
        self._typing_windows.append(toplevel)

    def _typing_elsewhere(self, focused):
        if focused is None or not self._typing_windows:
            return False
        try:
            is_text_box = focused.winfo_class() == "Entry" and focused.winfo_viewable()
        except tk.TclError:
            return False
        name = str(focused)
        return bool(is_text_box) and any(name.startswith(f"{window}.") for window in self._typing_windows)

    def _set_tap(self, text, icon, color, hold=True):
        """The card reader's pill on the entrance camera (_Feed.draw_tap) -
        held for RESET_DELAY_MS, then back to "Tap a card"."""
        self._tap = {"text": text, "icon": icon, "color": color}
        if self._card_reset_job:
            self.window.after_cancel(self._card_reset_job)
            self._card_reset_job = None
        if hold:
            self._card_reset_job = self.window.after(RESET_DELAY_MS, self._reset_card)

    def _show_card_waiting(self):
        self._set_tap("Tap a card", "identification-card", INK, hold=False)

    def _render_card_match(self, profile):
        """A registered card: the pill says so, and the tap lands in the live
        log like a face - with what the card scanner strip used to add
        (NOT ENROLLED, the person's distinguishing note, photos due) on the
        newest card there."""
        timestamp = datetime.now().strftime("%I:%M:%S %p")
        name = profile.get("name") or "Unknown"
        direction = (profile.get("direction") or self.direction or "entry").lower()
        notes = []
        if profile.get("not_enrolled"):  # not on this semester's enrolled list (dashboard: Semester)
            notes.append("NOT ENROLLED this semester")
        if profile.get("distinguishing_note"):
            notes.append(f"Note: {profile['distinguishing_note']}")
        if profile.get("photos_due"):  # Settings > Scheduling > "Show at the gate"
            notes.append("Photos due for update")
        # Let in (or out) by hand with Find person (gate_tools.py): the reason
        # and who decided ride on the row.
        manual = profile.get("manual_reason")
        if manual:
            by = f" - {profile['performed_by']}" if profile.get("performed_by") else ""
            notes.insert(0, f"By hand: {manual}{by}")
        how = "BY HAND" if manual else "CARD"
        kind, word, feature_word, extra = self._pass_kind(
            direction, profile.get("not_enrolled"), profile.get("pattern_flag"), "", card=how,
        )
        strip_word = f"{how} · AFTER HOURS" if self._after_hours else f"{how} · {direction.upper()}"
        caution = self._after_hours or kind in ("pattern", "not_enrolled")
        verb = ("Let out by hand" if direction == "exit" else "Let in by hand") if manual else "Card accepted"
        self._set_tap(f"{verb} · {name}", "warning" if caution else "check-circle",
                      CAUTION if caution else VERIFIED)
        if self._student is not None and direction == "entry":
            # Their photo and full name, briefly, as for a recognized face.
            self._student.show_card_result("accepted", profile)
        self._push_log_row(
            kind=kind, name=name, id_text=profile.get("student_id") or "—", time=timestamp,
            word=word or strip_word, feature_word=feature_word or strip_word, photo_bytes=profile.get("photo_bytes"),
            initials_seed=name, extra=extra, direction=direction, details=profile.get("details"),
            note=" · ".join(notes) or None,
        )

    def _render_card_failure(self, reason, reason_code):
        timestamp = datetime.now().strftime("%I:%M:%S %p")
        card_id = self._last_card_id
        if reason_code == "offline":
            # Not a rejection - the backend just couldn't be reached, and the
            # tap is already sitting in the offline queue.
            self._set_tap("Tap queued — will sync", "cloud-slash", CAUTION)
            kind, label = "card_offline", "Tap queued"
        else:
            label = self.FAILURE_HEADLINES.get(reason_code, "Access denied")
            self._set_tap("Card not registered" if reason_code == "not_registered" else label, "x-circle", DANGER)
            kind = "card_rejected"
        if self._student is not None and not (reason_code or "").startswith("staff_"):
            # A guard's own staff card is none of the student display's
            # business. Only a card the system actually looked up and refused reads as
            # "not registered" - a misread or a server hiccup asks for a retap.
            outcome = {"offline": "queued", "not_registered": "rejected", "deactivated": "deactivated",
                       "not_enrolled": "not_enrolled"}.get(reason_code, "error")
            self._student.show_card_result(outcome)
        # The backend's own reason, when it says more than the headline
        # (e.g. "Rejected: check SERVICE_TOKEN ...").
        note = reason if kind == "card_rejected" and reason and reason.rstrip(".") != label else None
        self._push_log_row(
            kind=kind, name=label if kind == "card_rejected" else "Card tap queued",
            id_text=f"Card {card_id}" if card_id else "Card", time=timestamp,
            word="CARD REJECTED" if kind == "card_rejected" else "QUEUED",
            feature_word=f"CARD REJECTED · {label}" if kind == "card_rejected" else "OFFLINE · TAP QUEUED",
            note=note,
        )

    def _reset_card(self):
        self._card_reset_job = None
        if self._closed:
            return
        self._show_card_waiting()

    # ---- camera events ----------------------------------------------------

    def _render_recognitions(self, recognitions, image_size, direction="entry"):
        """One scanned frame's answer from the `direction` camera (entrance
        or exit): its boxes go on that camera's feed; new events go into the
        one live log, counters and alarms."""
        feed = self._feeds.get(direction)
        if feed is not None:  # the exit camera may have just been switched off
            feed.recognitions = recognitions
            feed.image_size = image_size
        self._refresh_in_frame()
        corrected = {log_id for item in recognitions for log_id in item.get("corrected_log_ids") or []}
        if corrected:
            self._undo_unknowns(corrected)

        # A covered face is never logged (no log_id), so it can't go through
        # the new-event loop below - its soft sound plays once per person,
        # when a covered face shows up where none was a moment ago.
        covered = [item["box"] for item in recognitions if item.get("occlusion_suspected") and item.get("box")]
        tracker = self._covered_faces.setdefault(direction, CoveredFaces())
        if tracker.new_arrivals(covered) and self._sound_on["covered"]:
            self._sounds.play("covered")

        for item in recognitions:
            log_id = item.get("log_id")
            is_new_event = bool(log_id) and not item.get("deduped") and log_id not in self._seen_log_ids
            if not is_new_event:
                continue
            self._seen_log_ids.add(log_id)
            item.setdefault("direction", direction)
            if direction == "exit" and not item["matched"] and not item.get("refused_not_enrolled"):
                self.exit_stats["spoof" if item.get("spoof_suspected") else "unknown"] += 1
            if item.get("spoof_suspected"):
                self.stats["spoof"] += 1
                # Still counted and logged either way - the Settings page's
                # "Alert on suspected fake face" only silences the alarm.
                if self._alert_spoof:
                    self._show_alert_banner("spoof", direction=direction)
                    self._sounds.play("spoof")
            elif item.get("occlusion_suspected"):
                # Only reached by an older backend - covered faces no longer
                # come with a log_id (see IdentifyView._occlusion_prompt), so
                # the face's own "PLEASE UNCOVER YOUR FACE" label is all they get.
                self.stats["occlusion"] += 1
            elif item.get("refused_not_enrolled"):
                # A known student refused (not enrolled this semester) - not an
                # Unknown, so no Unknown alarm; its own "uh-oh" sound instead.
                if self._sound_on["not_enrolled"]:
                    self._sounds.play("not_enrolled")
            elif item["matched"]:
                self.stats["exits" if direction == "exit" else "entries"] += 1
            else:
                # An unknown face leaving is still someone unregistered who
                # was inside - same alarm, on the exit camera's panel.
                self.stats["unknown"] += 1
                if item.get("repeated_unknown"):
                    count = item.get("repeated_unknown_count")
                    sub = f"Seen {count} times at this gate recently — check this person" if count else None
                    self._show_alert_banner("repeated", sub, direction=direction)
                else:
                    self._show_alert_banner("unknown", direction=direction)
                if self._sound_on["unknown"]:
                    self._sounds.play("unknown")
            # The face as the camera saw it, for the Verify wall's LIVE tile.
            item["live_image"] = self._crop_live_face(feed, item, image_size)
            self._push_log_entry(item)

        self._refresh_stat_labels()
        self._refresh_exit_counts()
        if self._student is not None and direction == "entry":
            self._student.update_recognitions(recognitions, image_size)

    def _crop_live_face(self, feed, item, image_size):
        """The face, cut from the camera's latest picture with a margin (a
        PIL image), or None. The box is in the checked frame's pixels, which
        is the preview frame shrunk - so it's scaled back up."""
        box = item.get("box")
        if feed is None or not box or not image_size or image_size[0] <= 1:
            return None
        try:
            import cv2  # already loaded by the camera

            frame = feed.get_preview_frame()
            if frame is None:
                return None
            height, width = frame.shape[:2]
            scale = width / image_size[0]
            left, top = box["left"] * scale, box["top"] * scale
            right, bottom = box["right"] * scale, box["bottom"] * scale
            margin_x, margin_y = (right - left) * 0.25, (bottom - top) * 0.3
            x0, y0 = max(0, int(left - margin_x)), max(0, int(top - margin_y))
            x1, y1 = min(width, int(right + margin_x)), min(height, int(bottom + margin_y))
            if x1 - x0 < 8 or y1 - y0 < 8:
                return None
            return Image.fromarray(cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2RGB))
        except Exception:
            return None

    def _undo_unknowns(self, log_ids):
        """The backend undid these Unknown entries - the same face was just
        recognized (see IdentifyView._correct_recent_unknowns). Takes their
        rows out of the live log, counts them out of Unknown today, and
        drops an Unknown alarm banner that was about them."""
        removed = [entry for entry in self._log_entries if entry.get("log_id") in log_ids]
        if not removed:
            return
        featured_before = self._log_entries[0]
        self._log_entries = [entry for entry in self._log_entries if entry.get("log_id") not in log_ids]
        if self._view is not None:
            self._view.on_entries_removed(removed, featured_before)
        unknowns = sum(1 for entry in removed if entry["kind"] == "unknown")
        self.stats["unknown"] = max(0, self.stats["unknown"] - unknowns)
        if unknowns and self._alert and self._alert.get("kind") in ("unknown", "repeated"):
            if self._alert_hide_job:
                self.window.after_cancel(self._alert_hide_job)
            self._hide_alert_banner()
        self._refresh_stat_labels()

    def _show_alert_banner(self, kind, sub=None, direction="entry"):
        """Docks the alarm banner across the top of the video panel of the
        camera it's about - fires once per genuinely new unknown-face or spoof
        event (the same log_id dedup the stats/log rely on), not on every
        ~0.2s poll while the person is still in frame. Just sets state here -
        _Feed.draw_overlays, called on every video refresh tick, draws it."""
        self._alert = {"kind": kind, "time": datetime.now().strftime("%I:%M:%S %p"), "sub": sub,
                       "direction": direction}
        if self._alert_hide_job:
            self.window.after_cancel(self._alert_hide_job)
        self._alert_hide_job = self.window.after(self.ALERT_DISPLAY_MS, self._hide_alert_banner)

    def _hide_alert_banner(self):
        self._alert_hide_job = None
        self._alert = None
        # No explicit redraw needed - _update_video's own timer picks this up
        # and simply stops drawing the banner on its next tick.

    def _refresh_stat_labels(self):
        if self._view is not None:
            self._view.refresh_stats()  # the layout's own counters (Full camera draws its own each frame)

    # ---- live log ---------------------------------------------------------

    def _push_log_row(self, *, kind, name, id_text, time, word=None, feature_word=None,
                      conf=None, photo_bytes=None, initials_seed=None, extra="", log_id=None, direction="entry",
                      details=None, note=None, live_image=None):
        """The one way anything reaches the live log. Both credentials go
        through here - a face event from the scan loop and an NFC tap render
        as the same kind of row, differing only in their kind - so the log
        is a single chronological record of the gate rather than two
        half-stories. With the exit camera in its own window ("Separate
        window" layout), what happens at the exit goes to that window's log."""
        if direction == "exit" and self._exit_window is not None:
            self._exit_window.add_row({
                "kind": kind, "name": name, "id_text": id_text, "time": time, "conf": conf, "extra": extra,
                "word": word or LOG_KINDS[kind]["word"], "details": details or {},
            })
            self._refresh_exit_counts()
            return
        previous = self._log_entries[0] if self._log_entries else None
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
            "log_id": log_id,  # so an undone Unknown can be taken out again (_undo_unknowns)
            # STUDENT/STAFF, school or office, department, role (Settings > Live Log Details).
            "details": details or {},
            # After a card tap: NOT ENROLLED, a distinguishing note, photos due.
            "note": note,
            # The face as the camera saw it (the Verify wall's LIVE tile).
            "live_image": live_image,
            "direction": direction,  # IN / OUT on the Entrance + Exit log
        })
        # Only what changed is drawn (the layout's view decides - Split draws
        # just the new featured card and the row it replaces). Rebuilding all
        # ~60 rows on every event froze the gate monitor for 1-2 s each time.
        for dropped in self._log_entries[self.MAX_LOG_ROWS:]:
            self._destroy_log_row(dropped)
        self._log_entries = self._log_entries[: self.MAX_LOG_ROWS]
        if self._view is not None:
            self._view.on_new_entry(previous)

    def _render_after_hours(self, after_hours):
        """The header pill: ENTRY during opening hours, a caution AFTER HOURS
        outside them - and while it's after hours, every recognized entry
        and card tap lands in the live log as an AFTER HOURS row."""
        if after_hours == self._after_hours:
            return
        self._after_hours = after_hours
        self._render_mode_badge()

    def _render_mode_badge(self):
        if self._test_mode:
            return  # the pill keeps saying TEST MODE
        both = "exit" in self._feeds
        background, color, icon, word = (
            (CAUTION, "white", "moon", "AFTER HOURS") if self._after_hours
            else (SURFACE, MAROON_DEEP, "sign-in", "ENTRY + EXIT" if both else "ENTRY")
        )
        self._mode_badge.configure(fg_color=background)
        self._mode_icon.configure(text=_icon(icon), text_color=color, fg_color=background)
        self._mode_word.configure(text=word, text_color=color, fg_color=background)
        self._header_sub.configure(text="Face recognition + NFC card" + (" · 2 cameras" if both else ""))

    def _push_log_entry(self, item):
        # Every row from a face carries the camera's own crop of it (Verify wall).
        push = functools.partial(self._push_log_row, live_image=item.get("live_image"))
        timestamp = datetime.now().strftime("%I:%M:%S %p")
        # A moment of occlusion seen earlier in this same encounter, even
        # though it isn't this event's own outcome - appended to the meta
        # line rather than dropped, per EntryLog.occlusion_detected.
        extra = " · face briefly covered" if item.get("occlusion_seen") else ""
        leaving = item.get("direction") == "exit"
        at_exit = " · AT EXIT" if leaving else ""
        if item.get("spoof_suspected"):
            push(
                kind="spoof", name="Possible spoof", id_text="Liveness check failed", time=timestamp,
                word="SPOOF · EXIT" if leaving else None, feature_word=f"SPOOF SUSPECTED{at_exit}",
                photo_bytes=item.get("photo_bytes"), extra=extra, direction=item.get("direction", "entry"),
            )
        elif item.get("occlusion_suspected"):
            push(
                kind="covered", name="Face covered", id_text="Mouth/nose covered", time=timestamp,
                feature_word="PLEASE UNCOVER YOUR FACE", photo_bytes=item.get("photo_bytes"),
            )
        elif item.get("refused_not_enrolled"):
            push(
                kind="not_enrolled_refused", name=item["name"], id_text=item.get("student_id") or "—",
                time=timestamp, feature_word="NOT ENROLLED · REFUSED", conf=item.get("confidence"),
                photo_bytes=item.get("photo_bytes"), initials_seed=item["name"], extra=extra,
                details=item.get("details"),
            )
        elif item["matched"]:
            # Settings > Scheduling: "Show at the gate" for someone whose face
            # photos should be retaken.
            if item.get("photos_due"):
                extra += " · photos due for update"
            kind, word, feature_word, extra = self._pass_kind(
                "exit" if leaving else "entry", item.get("not_enrolled"), item.get("pattern_flag"), extra,
            )
            push(
                kind=kind, name=item["name"],
                id_text=item.get("student_id") or "—", time=timestamp,
                word=word, feature_word=feature_word,
                conf=item.get("confidence"), photo_bytes=item.get("photo_bytes"),
                initials_seed=item["name"], extra=extra, direction=item.get("direction", "entry"),
                details=item.get("details"),
            )
        else:
            push(
                kind="unknown", name="Unknown", id_text="No ID on file", time=timestamp,
                word="UNKNOWN · EXIT" if leaving else None, feature_word=f"UNKNOWN{at_exit}",
                photo_bytes=item.get("photo_bytes"), extra=extra, log_id=item.get("log_id"),
                direction=item.get("direction", "entry"),
            )

    def _pass_kind(self, way, not_enrolled, pattern_flag, extra, card=False):
        """(kind, row word, featured word, extra) for someone let through
        `way` ("entry" or "exit"), by face or by card. In order: not enrolled
        this semester (coming in only), an odd entry/exit pattern
        (PATTERN_WORDS - the guard should ask), after hours, or a plain pass.
        `card`: True for a card tap, or the word to use instead of CARD
        ("BY HAND" - let in with Find person)."""
        how = card if isinstance(card, str) else "CARD"
        base = (f"{how} · " if card else "") + way.upper()
        hours = " · AFTER HOURS" if self._after_hours else ""
        if not_enrolled and way == "entry":
            return "not_enrolled", "NOT ENROLLED", f"NOT ENROLLED{hours}", extra
        if pattern_flag in PATTERN_WORDS:
            word, note = PATTERN_WORDS[pattern_flag]
            return "pattern", word, f"{base} · {word}{hours}", f"{extra} · {note}"
        if self._after_hours:
            return "after_hours", "AFTER HOURS" + (" · EXIT" if way == "exit" else ""), f"{base} · AFTER HOURS", extra
        if card:
            return "card", base, base, extra
        return way, None, None, extra

    def _meta(self, entry):
        conf = f" · {entry['conf']}%" if entry.get("conf") is not None else ""
        return f"{entry['id_text']} · {entry['time']}{conf}{entry['extra']}"

    def _show_details(self, details, frame, kind_label, text_label, width, font, before=None):
        """Fills a details line - the STUDENT/STAFF tag and "school ·
        department · role" (Settings > Live Log Details) - and shows it, or
        hides it when the person has none switched on."""
        details = details or {}
        kind = details.get("kind")
        text = details_line(details)
        if not kind and not text:
            frame.pack_forget()
            return
        kind_label.pack_forget()
        text_label.pack_forget()
        if kind:
            kind_label.configure(text=f" {kind.upper()} ", fg_color=KIND_COLORS.get(kind, INK_600))
            kind_label.pack(side="left", padx=(0, 8))
        if text:
            tag_w = self._tkfont(COND_HEAVY, 14).measure(f" {kind.upper()} ") + self._px(16) if kind else 0
            text_label.configure(text=_ellipsize(self._tkfont(*font[:2]), text, max(self._px(60), width - tag_w)))
            text_label.pack(side="left", fill="x")
        if not frame.winfo_manager():
            frame.pack(fill="x", anchor="w", pady=(4, 0), **({"before": before} if before is not None else {}))

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

        if "_thumb_feat" not in entry:  # made once - Tk also needs the image kept referenced
            entry["_thumb_feat"] = self._thumb(entry, s["feat_thumb"])
        self.feat_thumb.configure(image=entry["_thumb_feat"])
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
        self._show_details(entry.get("details"), self.feat_details, self.feat_kind, self.feat_detail_text,
                           self._px(wrap), (FONT, 15), before=self.feat_id)
        self.feat_id.configure(text=entry["id_text"] + entry["extra"])
        if entry.get("note"):
            self.feat_note.configure(text=entry["note"], wraplength=wrap)
            self.feat_note.pack(fill="x", anchor="w", pady=(4, 0))
        else:
            self.feat_note.pack_forget()
        if entry.get("conf") is not None:
            self.feat_conf.configure(text=f"{entry['conf']}%")
            self.feat_match.grid()
        else:
            self.feat_match.grid_remove()

    def _rebuild_log_list(self):
        """Draws the whole live log from scratch - only when the window is
        built. Every later change draws just the rows it touches
        (_push_log_row, _undo_unknowns, _relayout_log)."""
        for child in self.log_list.winfo_children():
            child.destroy()
        self._log_placeholder = None
        for entry in self._log_entries:
            entry.pop("_row", None)
        self._show_featured_entry()
        # The newest rows only - a layout switch shouldn't stall on all 60;
        # older ones are a scroll away in the other layouts' history anyway.
        for entry in self._log_entries[1:self.REBUILD_ROWS]:
            self._add_log_row(entry)
        self._refresh_log_placeholder()

    def _show_featured_entry(self):
        """The newest event in the big card at the top, or "no events yet"."""
        if not self._log_entries:
            self.feat_card.grid_remove()
            self.feat_empty.grid(row=1, column=0, sticky="w", pady=(4, 0))
            return
        self.feat_empty.grid_remove()
        self.feat_card.grid()
        self._render_featured(self._log_entries[0])

    def _refresh_log_placeholder(self):
        has_rows = any("_row" in entry for entry in self._log_entries[1:])
        if has_rows and self._log_placeholder is not None:
            self._log_placeholder.destroy()
            self._log_placeholder = None
        elif not has_rows and self._log_placeholder is None:
            self._log_placeholder = self._label(self.log_list, "Earlier events appear here.", (FONT, 14), INK_400)
            self._log_placeholder.pack(anchor="w", padx=14, pady=14)

    def _log_fonts(self):
        s = self._s
        return self._tkfont(FONT, s["row_name"], "bold"), self._tkfont(FONT_MONO_MEDIUM, 14)

    def _log_name_width(self, word_w):
        """How wide a row's name and detail line may be. The scrollable
        frame's own width is its content's width, not the visible width -
        measure the panel instead, less the thumb column, this row's status
        word, paddings and the scrollbar."""
        panel_width = self._log_panel.winfo_width()
        if panel_width <= 1:
            panel_width = self._px(560)  # not laid out yet - _on_log_configure re-fits it once it is
        return panel_width - self._px(self._s["row_thumb"] + 28 + 40) - word_w

    def _add_log_row(self, entry):
        """Draws one entry's row in the list, in its place: newest at the top,
        so just above the row of the next older entry (or at the end)."""
        entry["_row"] = self._build_log_row(entry)
        index = self._log_entries.index(entry)
        below = next((other["_row"]["box"] for other in self._log_entries[index + 1:] if "_row" in other), None)
        if below is not None:
            entry["_row"]["box"].pack(fill="x", before=below)
        else:
            entry["_row"]["box"].pack(fill="x")

    def _destroy_log_row(self, entry):
        row = entry.pop("_row", None)
        if row is not None:
            row["box"].destroy()

    def _build_log_row(self, entry):
        s = self._s
        view = LOG_KINDS[entry["kind"]]
        bg = view.get("tint", SURFACE)
        row_font, meta_font = self._log_fonts()
        word_font = self._tkfont(COND_HEAVY, s["row_word"])
        box = tk.Frame(self.log_list, bg=SURFACE)
        row = ctk.CTkFrame(box, fg_color=bg, corner_radius=0)
        row.pack(fill="x")
        row.grid_columnconfigure(2, weight=1)
        ctk.CTkFrame(row, fg_color=view["color"] if view.get("sec") else bg, width=3, height=1, corner_radius=0).grid(
            row=0, column=0, sticky="ns"
        )
        if "_thumb_list" not in entry:  # made once - Tk also needs the image kept referenced
            entry["_thumb_list"] = self._thumb(entry, s["row_thumb"])
        ctk.CTkLabel(row, image=entry["_thumb_list"], text="", fg_color=bg).grid(row=0, column=1, padx=(14, 14),
                                                                                  pady=s["row_pad"])

        text = ctk.CTkFrame(row, fg_color=bg, corner_radius=0)
        text.grid(row=0, column=2, sticky="ew")
        word_w = word_font.measure(entry["word"]) + self._px(s["row_word"] + 6 + 10 + 16)
        name_width = self._log_name_width(word_w)
        name = self._label(
            text, _ellipsize(row_font, entry["name"], name_width), (FONT, s["row_name"], "bold"), INK,
            anchor="w", fg_color=bg,
        )
        name.pack(fill="x", anchor="w")
        # STUDENT/STAFF + school · department · role (Settings > Live Log Details).
        details = ctk.CTkFrame(text, fg_color=bg, corner_radius=0)
        kind = self._label(details, "", (COND_HEAVY, 12), "white", corner_radius=3, height=18)
        detail_text = self._label(details, "", (FONT, 13), INK_600, anchor="w", fg_color=bg)
        self._show_details(entry.get("details"), details, kind, detail_text, name_width, (FONT, 13))
        meta = self._label(
            text, _ellipsize(meta_font, self._meta(entry), name_width), (FONT_MONO_MEDIUM, 14), INK_600,
            anchor="w", fg_color=bg,
        )
        meta.pack(fill="x", anchor="w")

        word = ctk.CTkFrame(row, fg_color=bg, corner_radius=0)
        word.grid(row=0, column=3, sticky="e", padx=(10, 16))
        self._icon_label(word, view["icon"], s["row_word"], view["color"], fg_color=bg).pack(side="left", padx=(0, 6))
        self._label(word, entry["word"], (COND_HEAVY, s["row_word"]), view["color"], fg_color=bg).pack(side="left")

        # Plain tk.Frame - a CTkFrame this thin draws nothing at all.
        tk.Frame(box, bg=LINE, height=max(1, self._px(1))).pack(fill="x")
        return {"box": box, "name": name, "meta": meta, "word_w": word_w}

    # ---- video panel ------------------------------------------------------

    def _update_video(self):
        if self._closed:
            return
        started = time.perf_counter()
        # The alarm started or ended: layouts that show it as a bar of their
        # own (Verify wall, Entrance + Exit) hear about it here.
        alert_key = self._alert_state_key()
        if alert_key != self._alert_key and self._view is not None:
            self._alert_key = alert_key
            try:
                self._view.on_alert(self._alert)
            except Exception:
                traceback.print_exc()
        for feed in list(self._feeds.values()):
            try:
                if not feed.canvas.winfo_exists():
                    continue  # this layout isn't showing that camera right now
                feed.refresh()
            except Exception:
                # Logged, then the next frame tries again - an escaped error
                # here used to skip the reschedule and freeze the feed for good.
                traceback.print_exc()
        # The next frame is due one interval after this one STARTED - waiting
        # the whole interval after drawing made 20 a second come out at ~13.
        # But never sooner than VIDEO_MIN_GAP_MS after drawing ends.
        spent_ms = (time.perf_counter() - started) * 1000
        self.window.after(max(VIDEO_MIN_GAP_MS, round(self._video_refresh_ms - spent_ms)), self._update_video)

    def _strip_height(self):
        return self._px(self._s["strip"])

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
        if item.get("refused_not_enrolled"):
            # Recognized, but refused: not on this semester's enrolled list.
            return DANGER, "warning-octagon", f"{item['name']} · NOT ENROLLED"
        if item["matched"]:
            label = item["name"]
            if item.get("confidence") is not None:
                label = f"{label} · {item['confidence']}%"
            if item.get("not_enrolled"):
                # Let in, but not on this semester's enrolled list - caution,
                # so the guard can check them.
                return CAUTION, "warning", f"{label} · NOT ENROLLED"
            return VERIFIED, "sign-out" if item.get("direction") == "exit" else "sign-in", label
        return CAUTION, "user-circle-dashed", "UNKNOWN"

    def _confirm_close(self):
        """Escape or the window's X: close only when the guard says so."""
        if self._closed:
            return
        on_duty = (self._sign_in.get("on_duty") or {}).get("name")
        detail = "Face scanning and card taps stop at this gate until it's opened again."
        if on_duty:
            detail += f" {on_duty} is signed out of duty."
        if messagebox.askyesno("Close the gate monitor?", detail, icon="warning", default="no", parent=self.window):
            self._handle_close()

    def _handle_close(self):
        self._closed = True
        self._close_layout_menu()
        self._close_sign_in_dialog()
        self._close_setup_dialog()
        if self._exit_window is not None:
            self._exit_window.close(notify=False)
        if self._student is not None:
            self._student.close()  # it's a child window - it goes when the monitor does
        if self._on_close:
            self._on_close()
        self.window.destroy()
