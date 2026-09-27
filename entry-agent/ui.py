import io
import math
import os
import queue
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import winsound
from datetime import datetime

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageTk

# ---------------------------------------------------------------------------
# Design system (colors/fonts match the EVSU SecureTap UI redesign spec)
# ---------------------------------------------------------------------------

MAROON = "#7B1113"
MAROON_DARK = "#5A0C0E"
MAROON_LIGHT = "#F0C9CA"
BG = "#F5F5F7"
CARD_BG = "#FFFFFF"
BORDER = "#E2E2E6"
TEXT_PRIMARY = "#1A1A1A"
TEXT_SECONDARY = "#6B6B70"
TEXT_MUTED = "#9A9AA0"
SUCCESS = "#34C759"
DANGER = "#FF3B30"
WARNING = "#FF9F0A"
ACCENT = "#0A84FF"
# A face whose mouth/nose read as covered - its own color, not reused from
# WARNING (Unknown) or DANGER (spoof), since the whole point is that this
# reads as its own distinct status rather than folding into either of those.
OCCLUSION = "#30B0C7"
PLACEHOLDER_AVATAR = "#D1D5DB"
SURFACE_ALT = "#F7F7F9"  # tinted inner surface - waiting states, chips, count badges
VIDEO_BG = "#0B1220"  # near-black panel behind the camera feed

FONT = "Segoe UI"
FONT_MONO = "Consolas"

FOCUS_CHECK_MS = 300
RESET_DELAY_MS = 8000
VIDEO_REFRESH_MS = 42  # ~24 fps

ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
ICON_PATH = os.path.join(ASSETS_DIR, "icon.png")
# A proper multi-resolution .ico, generated from icon.png (same EVSU seal) -
# see _apply_icon's docstring for why this exists alongside the PNG.
ICON_ICO_PATH = os.path.join(ASSETS_DIR, "icon.ico")

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")


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
    if there's no photo to show. Deliberately always a plain rectangle, not
    a circular crop - a real person's photo stays square; only the no-photo
    placeholder badge (see _initials_avatar) is ever circular."""
    if not raw_bytes:
        return None
    try:
        source = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
        return ImageOps.fit(source, (size, size), Image.LANCZOS)
    except Exception:
        return None


def _initials_avatar(name, size, bg=MAROON):
    """A circular initials badge - used only as a placeholder when there's
    no real photo, so it reads clearly as "no photo on file" rather than as
    a distorted picture of someone."""
    initials = "".join(part[0] for part in (name or "?").split()[:2]).upper() or "?"
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((0, 0, size - 1, size - 1), fill=bg)
    font = None
    try:
        font_path = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "segoeuib.ttf")
        font = ImageFont.truetype(font_path, size=int(size * 0.38))
    except Exception:
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None
    if font is not None:
        bbox = draw.textbbox((0, 0), initials, font=font)
        text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(
            ((size - text_w) / 2 - bbox[0], (size - text_h) / 2 - bbox[1]),
            initials, fill="white", font=font,
        )
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
    """Masks image's corners to transparent so it visually matches the
    rounded CTkFrame it's drawn inside (see GateMonitorWindow's video_frame)
    - a plain tk.Canvas draws a flat rectangle no matter what, so without
    this the live feed's own square corners poke past the frame's curve at
    each corner, breaking the rounded look. Tkinter's PhotoImage renders an
    RGBA image's alpha correctly on a Canvas, so this is enough on its own -
    no separate background fill needed, since the canvas underneath is
    already the same color the frame is."""
    image = image.convert("RGBA")
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, image.size[0] - 1, image.size[1] - 1), radius=radius, fill=255
    )
    image.putalpha(mask)
    return image


def _rounded_rect_points(x0, y0, x1, y1, radius, steps_per_corner=12):
    """Points for a rounded rectangle as a Canvas polygon, for something
    this simple (a small, static-shaped overlay like the "LIVE MONITOR"
    badge) without needing a rebuilt PIL image + PhotoImage to keep alive
    every frame - the video panel's own rounding (a much bigger,
    photographic image) still goes through _round_corners, where an actual
    alpha mask is worth it.

    Samples each corner as a real quarter-circle arc (steps_per_corner
    points), rather than the 2-points-per-corner + smooth=True spline this
    used originally - Tk's spline only approximates the corner from those 2
    points and visibly undershoots the requested radius, reading as
    "softened corners" rather than a genuinely round/pill end. Sampling the
    actual arc trigonometrically draws the real curve regardless of
    whether the caller also passes smooth=True to create_polygon."""
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


def _hex_to_rgb(color):
    color = color.lstrip("#")
    return tuple(int(color[i : i + 2], 16) for i in (0, 2, 4))


def _rgb_to_hex(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(c))) for c in rgb)


def _color_ramp(start_hex, end_hex, steps):
    """A list of `steps + 1` hex colors interpolated evenly from start to
    end - used to animate a hover color change frame by frame instead of
    just snapping to the new color."""
    start, end = _hex_to_rgb(start_hex), _hex_to_rgb(end_hex)
    return [
        _rgb_to_hex(tuple(start[c] + (end[c] - start[c]) * i / steps for c in range(3)))
        for i in range(steps + 1)
    ]


class _HoverAnimator:
    """Animates a widget's fg_color between two colors as the cursor enters
    and leaves it, instead of snapping instantly - a short, cheap
    step-through of precomputed colors via widget.after(), not a fragile
    canvas trick. Bind the returned enter()/leave() methods to <Enter>/
    <Leave> on every widget that makes up the hoverable area (a frame plus
    its child labels, say), since CTk widgets don't bubble mouse events
    from children up to their parent."""

    STEPS = 8
    INTERVAL_MS = 12

    def __init__(self, widget, from_color, to_color):
        self.widget = widget
        self._ramp = _color_ramp(from_color, to_color, self.STEPS)
        self._index = 0
        self._job = None

    def enter(self, _event=None):
        self._animate_to(len(self._ramp) - 1)

    def leave(self, _event=None):
        self._animate_to(0)

    def _animate_to(self, target_index):
        if self._job is not None:
            self.widget.after_cancel(self._job)
            self._job = None
        self._step(target_index)

    def _step(self, target_index):
        if not self.widget.winfo_exists() or self._index == target_index:
            self._job = None
            return
        self._index += 1 if target_index > self._index else -1
        self.widget.configure(fg_color=self._ramp[self._index])
        self._job = self.widget.after(self.INTERVAL_MS, lambda: self._step(target_index))


# ---------------------------------------------------------------------------
# Shared components
# ---------------------------------------------------------------------------


class HeaderBar(ctk.CTkFrame):
    """Maroon bar: title + subtitle on the left, an optional badge (e.g. an
    ENTRY/EXIT pill) and a live clock on the right. Used at the top of all
    three windows so they read as one consistent app."""

    LOGO_SIZE = 72

    def __init__(self, parent, title, subtitle="", show_clock=False, badge=None, center=False, logo_path=None):
        super().__init__(parent, fg_color=MAROON, corner_radius=0)
        self.grid_columnconfigure(0, weight=1)
        self.badge_label = None
        self.clock_label = None

        if center:
            # The launcher's header - a centered logo (if given) above the
            # title/subtitle, no clock or badge, matching the design
            # reference exactly.
            block = ctk.CTkFrame(self, fg_color="transparent")
            block.grid(row=0, column=0, pady=(26, 22))
            if logo_path and os.path.isfile(logo_path):
                try:
                    logo_source = Image.open(logo_path).convert("RGBA")
                    logo_image = ctk.CTkImage(
                        light_image=logo_source, dark_image=logo_source, size=(self.LOGO_SIZE, self.LOGO_SIZE)
                    )
                    logo_label = ctk.CTkLabel(block, image=logo_image, text="")
                    logo_label.pack(pady=(0, 10))
                    self._logo_image_ref = logo_image  # keep a reference or Tk garbage-collects it
                except Exception:
                    pass  # missing/corrupt logo asset shouldn't block the launcher from opening
            ctk.CTkLabel(block, text=title, font=(FONT, 24, "bold"), text_color="white").pack()
            if subtitle:
                ctk.CTkLabel(block, text=subtitle, font=(FONT, 13), text_color=MAROON_LIGHT).pack(pady=(6, 0))
            return

        left = ctk.CTkFrame(self, fg_color="transparent")
        left.grid(row=0, column=0, sticky="w", padx=20, pady=14)
        ctk.CTkLabel(left, text=title, font=(FONT, 17, "bold"), text_color="white").pack(anchor="w")
        if subtitle:
            ctk.CTkLabel(left, text=subtitle, font=(FONT, 11), text_color=MAROON_LIGHT).pack(anchor="w")

        right = ctk.CTkFrame(self, fg_color="transparent")
        right.grid(row=0, column=1, sticky="e", padx=20, pady=14)

        if badge:
            self.badge_label = ctk.CTkLabel(
                right, text=badge, font=(FONT, 11, "bold"), text_color="white",
                fg_color=MAROON_DARK, corner_radius=10,
            )
            self.badge_label.pack(side="left", padx=(0, 12), ipadx=8, ipady=2)

        self.clock_label = None
        if show_clock:
            self.clock_label = ctk.CTkLabel(right, text="", font=(FONT, 11), text_color=MAROON_LIGHT)
            self.clock_label.pack(side="left")
            self._tick()

    def set_badge(self, text):
        if self.badge_label:
            self.badge_label.configure(text=text)

    def _tick(self):
        if not self.winfo_exists():
            return
        self.clock_label.configure(text=f"● LIVE · {datetime.now().strftime('%I:%M:%S %p')}")
        self.after(1000, self._tick)


# ---------------------------------------------------------------------------
# The gate monitor - the entry-agent's only window
# ---------------------------------------------------------------------------


class GateMonitorWindow:
    """The entry-agent's only window, owning both credentials at once - the
    continuous camera check and the NFC card reader - so a guard watches one
    screen instead of alt-tabbing between two.

    There is deliberately no menu in front of this. Picking "Entry Agent" in
    the system launcher (launcher.py at the repo root) should land on the
    scanners, the live log and the feed - not on a second screen asking again
    what the user already said. So this owns the CTk root itself, and closing
    it ends the process.

    The layout ranks the three panels by how much a guard actually looks at
    them. The live log is the widest panel and the one that grows with the
    window, because it's the running record of who came through and the
    thing worth reading. The camera feed takes the entire remaining height
    of the left column. The card scanner sits under it as a compact strip
    that keeps its natural height (its grid row has weight 0, so every spare
    pixel goes to the feed above) - a tap is an occasional cross-check, not
    the primary flow.

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
    # A plain tk.Canvas can't be rounded as a widget, and insetting it away
    # from video_frame's own rounded edge (an earlier attempt at this) just
    # trades the problem for a visible border on every side instead of only
    # the corners. The actual fix: the canvas fills its cell edge-to-edge
    # with zero inset, and every draw composites one full-canvas-sized RGBA
    # image (backdrop + live frame + recognition boxes' backing) and masks
    # THAT to this radius before handing it to the canvas - see
    # _draw_frame/_draw_no_camera_placeholder and _round_corners. The
    # rounding is baked into the image itself, not faked with layout.
    VIDEO_PANEL_CORNER_RADIUS = 16
    # How long with no frame at all before the video panel gives up waiting
    # and shows "No camera connected" instead of just sitting blank - long
    # enough that a real webcam's normal startup delay never trips it.
    CAMERA_GRACE_SECONDS = 3.0
    MAX_LOG_ROWS = 60
    LOG_GRID_COLUMNS = 4
    LOG_THUMB_SIZE = 88
    CARD_AVATAR_SIZE = 76
    ALERT_DISPLAY_MS = 6000

    FAILURE_HEADLINES = {
        "not_registered": "Not registered",
        "deactivated": "Deactivated",
        "read_error": "Read error",
        "offline": "Offline",
    }

    STAT_SPECS = (
        ("today", "Today", MAROON),
        ("entries", "Entries", SUCCESS),
        ("unknown", "Unknown", WARNING),
        ("spoof", "Spoof", DANGER),
        ("occlusion", "Occluded", OCCLUSION),
        ("in_frame", "In frame", ACCENT),
    )
    # Window width below which the 6 stat tiles wrap to 3-per-row instead of
    # squashing into one row - the window opens maximized by default, so
    # this mostly matters if a guard un-maximizes/resizes it narrower.
    STATS_WRAP_BREAKPOINT = 900
    STATS_COLUMNS_WIDE = len(STAT_SPECS)
    STATS_COLUMNS_NARROW = 3

    def __init__(self, gate_location, direction, get_preview_frame, on_tap,
                 officer_name="", version="", on_close=None,
                 camera_options=None, on_camera_change=None, initial_camera_index=None):
        self._get_preview_frame = get_preview_frame
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
        # logic covers all three cases the same way: never connected (this
        # just never advances), disconnected mid-session (stops advancing,
        # so the placeholder reappears after the grace period), and
        # reconnected (advances again the instant a frame arrives, so the
        # placeholder disappears again on its own). Starts at window-
        # creation time, not zero, so a real camera's normal brief startup
        # delay doesn't immediately read as "already been silent too long".
        self._last_frame_at = time.monotonic()
        self._seen_log_ids = set()
        self._log_entries = []  # newest first
        self._log_photo_images = []  # keeps CTkImage refs alive for the log list
        self.stats = {"entries": 0, "exits": 0, "unknown": 0, "spoof": 0, "occlusion": 0}
        self._alert_hide_job = None
        self._card_reset_job = None
        self._card_avatar_image = None

        # This IS the application window, not a child of some menu - the
        # entry-agent opens straight into the monitor. Being the CTk root (not
        # a CTkToplevel) is what makes closing it end the app, which is the
        # right behaviour when there's nothing behind it to return to.
        self.window = ctk.CTk()
        self.window.title(f"EVSU SecureTap - Gate monitor - {gate_location}")
        self.window.configure(fg_color=BG)
        # CustomTkinter multiplies geometry by the display's DPI scaling, so
        # these are deliberately conservative logical sizes - on a 125%
        # display 1200x720 is already 1500x900 real pixels. The window opens
        # maximized anyway (see below); this is just the restore size.
        self.window.geometry("1200x720")
        # Width floor lowered from 1100 to 820: at 1100 a guard could never
        # actually resize this window narrow enough to reach
        # STATS_WRAP_BREAKPOINT (900), which would make the stat-tile wrap
        # logic below unreachable through normal manual resizing.
        self.window.minsize(820, 680)
        self.window.protocol("WM_DELETE_WINDOW", self._handle_close)
        self.window.bind("<Escape>", lambda _e: self._handle_close())
        self.window.bind("<Configure>", self._on_window_configure)
        _apply_icon(self.window)

        self.window.grid_rowconfigure(2, weight=1)
        self.window.grid_columnconfigure(0, weight=1)

        self.header = HeaderBar(
            self.window, f"{gate_location} — live monitoring",
            subtitle="Face recognition + NFC card", show_clock=True, badge=direction.upper(),
        )
        self.header.grid(row=0, column=0, sticky="ew")

        self._build_stats_strip()
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
        # Open filling the screen - this is an operational display a guard
        # leaves up all shift, not a dialog. Deferred because CTkToplevel
        # re-applies its own window attributes shortly after construction,
        # and "zoomed" set inline gets clobbered by that. Windows-only state,
        # so a TclError elsewhere just leaves the restore geometry in place.
        self.window.after(300, self._maximize)
        if self._get_preview_frame:
            self.window.after(VIDEO_REFRESH_MS, self._update_video)

    def _maximize(self):
        if self._closed:
            return
        try:
            self.window.state("zoomed")
        except tk.TclError:
            pass

    # ---- layout ---------------------------------------------------------

    @staticmethod
    def _pad(text):
        """Pill-style CTkLabels get their horizontal breathing room from the
        text itself - `place`d and `grid`ed labels can't take pack's ipadx,
        so padding the string is the one approach that works everywhere."""
        return f"  {text}  "

    def _chip(self, parent, text, color):
        return ctk.CTkLabel(
            parent, text=self._pad(text), font=(FONT, 10), text_color=color,
            fg_color=CARD_BG, corner_radius=9, height=24,
        )

    def _build_stats_strip(self):
        self.stats_strip = ctk.CTkFrame(self.window, fg_color="transparent")
        self.stats_strip.grid(row=1, column=0, sticky="ew", padx=18, pady=(14, 8))

        self.stat_tiles = {}
        self._stat_tile_frames = []
        for key, label, color in self.STAT_SPECS:
            tile = ctk.CTkFrame(
                self.stats_strip, fg_color=CARD_BG, corner_radius=14, border_width=1, border_color=BORDER
            )
            head = ctk.CTkFrame(tile, fg_color="transparent")
            head.pack(fill="x", padx=16, pady=(12, 0))
            ctk.CTkLabel(head, text="●", font=(FONT, 9), text_color=color).pack(side="left", padx=(0, 6))
            ctk.CTkLabel(head, text=label.upper(), font=(FONT, 10, "bold"), text_color=TEXT_MUTED).pack(side="left")

            value_label = ctk.CTkLabel(tile, text="0", font=(FONT, 26, "bold"), text_color=TEXT_PRIMARY)
            value_label.pack(padx=16, pady=(0, 12), anchor="w")
            self.stat_tiles[key] = value_label
            self._stat_tile_frames.append(tile)

        self._stats_columns = None  # forces the first _relayout_stats call to actually apply
        self._relayout_stats(self.STATS_COLUMNS_WIDE)

    def _relayout_stats(self, columns):
        if columns == self._stats_columns:
            return
        self._stats_columns = columns
        for tile in self._stat_tile_frames:
            tile.grid_forget()
        for column in range(max(self.STATS_COLUMNS_WIDE, self.STATS_COLUMNS_NARROW)):
            # Reset every column this strip has ever used, not just the ones
            # about to be reused - otherwise a column dropped when going from
            # 6-wide to 3-narrow keeps its old weight/uniform tag and quietly
            # reserves dead space nothing sits in anymore.
            self.stats_strip.grid_columnconfigure(column, weight=0, uniform="")
        for index, tile in enumerate(self._stat_tile_frames):
            row, column = divmod(index, columns)
            tile.grid(
                row=row, column=column, sticky="ew",
                padx=(0 if column == 0 else 10, 0), pady=(0 if row == 0 else 10, 0),
            )
        for column in range(columns):
            self.stats_strip.grid_columnconfigure(column, weight=1, uniform="stat")

    def _on_window_configure(self, event):
        # Tkinter fires <Configure> for every child widget resize too, not
        # just the toplevel - without this guard, every stat tile's own
        # grid() call above would re-enter this handler.
        if event.widget is not self.window:
            return
        wrapped = event.width < self.STATS_WRAP_BREAKPOINT
        self._relayout_stats(self.STATS_COLUMNS_NARROW if wrapped else self.STATS_COLUMNS_WIDE)

    def _build_main_area(self):
        area = ctk.CTkFrame(self.window, fg_color="transparent")
        area.grid(row=2, column=0, sticky="nsew", padx=18, pady=(0, 10))
        area.grid_rowconfigure(0, weight=1)
        # The live log is the wider half (3 vs 2) - it's the panel a guard
        # reads, so it gets the space. `uniform` makes those weights an
        # actual 2:3 width ratio instead of just a split of leftover space.
        area.grid_columnconfigure(0, weight=2, uniform="main")
        area.grid_columnconfigure(1, weight=3, uniform="main")

        left = ctk.CTkFrame(area, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(0, weight=1)  # video absorbs all spare height;
        # the card panel's row stays weight 0 and keeps its natural height, so
        # the live monitor is always the taller of the two.

        self._build_video_panel(left)
        self._build_card_panel(left)
        self._build_log_panel(area)

    def _build_video_panel(self, parent):
        # Plain, unrounded container now - the canvas below fills it exactly
        # edge-to-edge and paints its own rounded panel directly (see
        # VIDEO_PANEL_CORNER_RADIUS's comment), so rounding this frame too
        # would be invisible at best and a mismatched double edge at worst.
        video_frame = ctk.CTkFrame(parent, fg_color=BG, corner_radius=0)
        video_frame.grid(row=0, column=0, sticky="nsew")
        video_frame.grid_rowconfigure(0, weight=1)
        video_frame.grid_columnconfigure(0, weight=1)

        # bg=BG (the page background), not VIDEO_BG - this shows only in the
        # instant before the first draw ever lands (see _draw_frame/
        # _draw_no_camera_placeholder), which paint a full-canvas rounded
        # VIDEO_BG panel over literally every pixel from then on, corners
        # included. No inset: the canvas fills video_frame exactly.
        self.video_canvas = tk.Canvas(video_frame, bg=BG, highlightthickness=0)
        self.video_canvas.grid(row=0, column=0, sticky="nsew")

        # The "LIVE MONITOR" badge and the caption below it used to be
        # separate CTkLabel widgets stacked on top of the canvas. A CTkLabel
        # is itself backed by its OWN small canvas that has to paint some
        # solid color across its whole bounding box first - Tk has no way
        # for one widget to show "whatever's behind it" from a different
        # sibling widget, so bg_color is always a flat, static fill, never
        # true transparency. That looked fine over the placeholder's flat
        # VIDEO_BG, but shows as an obviously wrong solid patch over real,
        # constantly-changing video - and no bg_color choice fixes that,
        # since the mismatch is structural, not a wrong color. Both are now
        # drawn directly ON the video canvas instead, every redraw (the same
        # approach _draw_box already uses for recognition labels) - a true
        # overlay, so the badge has no surrounding rectangle beyond its own
        # red pill, and the caption has no background at all. See
        # _draw_overlays, called at the end of both _draw_frame and
        # _draw_no_camera_placeholder. _caption_text is the plain string
        # state that replaces caption_label's old .configure(text=...) calls.
        self._caption_text = "Starting camera..."

        # The spoof/occlusion/unknown-person alert banner had the exact same
        # CTkLabel-over-live-video problem described above (fg_color=DANGER
        # pill, bg_color=VIDEO_BG filling the rounded corners' cutout with a
        # flat near-black fill instead of the live frame behind it - VIDEO_BG
        # is "#0B1220", near-black, which is why the corners specifically
        # looked solid black rather than some other obviously-wrong color).
        # Fixed the same way: no widget, just state read by _draw_overlays.
        # None means "not currently shown".
        self._alert_text = None

    def _build_card_panel(self, parent):
        """The compact card-scanner strip under the feed. Swaps between a
        "tap a card" prompt and a horizontal result row - horizontal because
        the panel is deliberately short, and because the full-height portrait
        card this replaces is no longer needed: every tap also lands in the
        live log next to it."""
        panel = ctk.CTkFrame(parent, fg_color=CARD_BG, corner_radius=16, border_width=1, border_color=BORDER)
        panel.grid(row=1, column=0, sticky="nsew", pady=(14, 0))
        panel.grid_columnconfigure(0, weight=1)

        head = ctk.CTkFrame(panel, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(13, 0))
        head.grid_columnconfigure(1, weight=1)
        ctk.CTkFrame(head, fg_color=MAROON, width=4, height=18, corner_radius=2).grid(row=0, column=0, padx=(0, 10))
        ctk.CTkLabel(head, text="Card scanner", font=(FONT, 14, "bold"), text_color=TEXT_PRIMARY).grid(
            row=0, column=1, sticky="w"
        )
        self.reader_label = ctk.CTkLabel(head, text="● Reader ready", font=(FONT, 10), text_color=SUCCESS)
        self.reader_label.grid(row=0, column=2, sticky="e")

        self.card_body = ctk.CTkFrame(panel, fg_color="transparent")
        self.card_body.grid(row=1, column=0, sticky="nsew", padx=16, pady=(10, 14))
        self.card_body.grid_columnconfigure(0, weight=1)
        self.card_body.grid_rowconfigure(0, weight=1)

        self.card_waiting = ctk.CTkFrame(self.card_body, fg_color=SURFACE_ALT, corner_radius=14)
        waiting_inner = ctk.CTkFrame(self.card_waiting, fg_color="transparent")
        waiting_inner.pack(expand=True, pady=20)
        ctk.CTkLabel(waiting_inner, text="(( • ))", font=(FONT, 22, "bold"), text_color=ACCENT).pack()
        ctk.CTkLabel(waiting_inner, text="Tap a card", font=(FONT, 15, "bold"), text_color=TEXT_PRIMARY).pack(
            pady=(8, 1)
        )
        ctk.CTkLabel(
            waiting_inner, text="Hold ID near the reader", font=(FONT, 11), text_color=TEXT_SECONDARY
        ).pack()

        self.card_result = ctk.CTkFrame(self.card_body, fg_color="transparent")
        self.card_result.grid_columnconfigure(1, weight=1)
        self.card_avatar = ctk.CTkLabel(self.card_result, text="")
        self.card_avatar.grid(row=0, column=0, sticky="n")

        info = ctk.CTkFrame(self.card_result, fg_color="transparent")
        info.grid(row=0, column=1, sticky="nsew", padx=(16, 0))
        self.card_status = ctk.CTkLabel(info, text="", font=(FONT, 12, "bold"), anchor="w")
        self.card_status.pack(fill="x")
        self.card_name = ctk.CTkLabel(info, text="", font=(FONT, 18, "bold"), text_color=TEXT_PRIMARY, anchor="w")
        self.card_name.pack(fill="x", pady=(3, 0))
        self.card_sub = ctk.CTkLabel(
            info, text="", font=(FONT, 11), text_color=TEXT_SECONDARY, anchor="w",
            wraplength=260, justify="left",
        )
        self.card_sub.pack(fill="x")
        # card_ids is packed/unpacked per result rather than just blanked: a
        # rejected tap has no IDs to show, and an empty label still reserves
        # its line - height the camera feed above should be getting instead.
        self.card_ids = ctk.CTkLabel(info, text="", font=(FONT_MONO, 11), text_color=TEXT_PRIMARY, anchor="w")
        self.card_ids.pack(fill="x", pady=(8, 0))
        # Same packed/unpacked-per-result pattern as card_ids above - a
        # distinguishing note (see users.models.Person.distinguishing_note)
        # only shows when this specific person has one on file.
        self.card_note = ctk.CTkLabel(
            info, text="", font=(FONT, 10, "bold"), text_color=WARNING, anchor="w",
            wraplength=260, justify="left",
        )
        self.card_time = ctk.CTkLabel(info, text="", font=(FONT, 10), text_color=TEXT_MUTED, anchor="w")
        self.card_time.pack(fill="x", pady=(4, 0))

    def _build_log_panel(self, parent):
        log_frame = ctk.CTkFrame(parent, fg_color=CARD_BG, corner_radius=16, border_width=1, border_color=BORDER)
        log_frame.grid(row=0, column=1, sticky="nsew")
        log_frame.grid_rowconfigure(1, weight=1)
        log_frame.grid_columnconfigure(0, weight=1)

        head = ctk.CTkFrame(log_frame, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=18, pady=(15, 8))
        head.grid_columnconfigure(1, weight=1)
        ctk.CTkFrame(head, fg_color=MAROON, width=4, height=22, corner_radius=2).grid(row=0, column=0, padx=(0, 10))
        ctk.CTkLabel(head, text="Live log", font=(FONT, 17, "bold"), text_color=TEXT_PRIMARY).grid(
            row=0, column=1, sticky="w"
        )
        self.log_count_label = ctk.CTkLabel(
            head, text=self._pad("0 events"), font=(FONT, 10, "bold"), text_color=TEXT_SECONDARY,
            fg_color=SURFACE_ALT, corner_radius=9, height=24,
        )
        self.log_count_label.grid(row=0, column=2, sticky="e")

        self.log_list = ctk.CTkScrollableFrame(log_frame, fg_color="transparent")
        self.log_list.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 12))
        for col in range(self.LOG_GRID_COLUMNS):
            self.log_list.grid_columnconfigure(col, weight=1, uniform="log_card")

    def _build_status_bar(self):
        bar = ctk.CTkFrame(self.window, fg_color="transparent")
        bar.grid(row=3, column=0, sticky="ew", padx=18, pady=(0, 14))
        self.threshold_label = self._chip(bar, "Recognition running", TEXT_SECONDARY)
        self.threshold_label.pack(side="left")
        # Camera health used to sit on the entry-agent's own launcher screen.
        # That screen is gone - the monitor opens directly now - so it moves
        # here rather than being dropped: a camera that stopped responding is
        # precisely what a guard needs to notice, and the feed going still
        # doesn't always look different from an empty gate.
        self.camera_label = self._chip(bar, "Camera ✓", SUCCESS)
        self.camera_label.pack(side="left", padx=(8, 0))
        self._build_camera_picker(bar)

        # Same for the officer/version footer the launcher used to carry.
        identity = " · ".join(part for part in (self.officer_name, self.version) if part)
        if identity:
            ctk.CTkLabel(bar, text=identity, font=(FONT, 10), text_color=TEXT_MUTED).pack(
                side="left", padx=(14, 0)
            )

        self.sync_label = self._chip(bar, "Synced", SUCCESS)
        self.sync_label.pack(side="right")
        self.queue_label = self._chip(bar, "Queue 0", TEXT_MUTED)
        self.queue_label.pack(side="right", padx=(0, 8))
        self.backend_label = self._chip(bar, "Backend ✓", SUCCESS)
        self.backend_label.pack(side="right", padx=(0, 8))

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
            width=190, height=24, font=(FONT, 10), dropdown_font=(FONT, 10),
            fg_color=CARD_BG, text_color=TEXT_PRIMARY,
            button_color=MAROON, button_hover_color=MAROON_DARK,
        )
        self.camera_picker.set(default_value)
        self.camera_picker.pack(side="left", padx=(8, 0))
        if not self._camera_options:
            # Nothing to switch to - still shown (so it's obvious the app
            # looked and found none), just not interactive.
            self.camera_picker.configure(state="disabled")

    def _handle_camera_picked(self, selected_value):
        index = self._camera_choices.get(selected_value)
        if index is not None and self._on_camera_change:
            self._on_camera_change(index)

    # ---- public API: camera side ------------------------------------------

    def show_recognitions(self, recognitions, image_size):
        """recognitions: list of dicts with box/matched/name/student_id/
        department/confidence/log_id/deduped, one per face in the latest
        scanned frame. image_size: (width, height) of that frame, so boxes
        (in that frame's pixel coordinates) can be scaled onto the preview."""
        self._queue.put(("recognitions", (recognitions, image_size)))

    def show_offline(self, offline):
        self._queue.put(("offline", offline))

    def seed_stats(self, entries_today, exits_today, unknown_today, spoof_today=0, occlusion_today=0):
        self._queue.put(("seed", (entries_today, exits_today, unknown_today, spoof_today, occlusion_today)))

    def set_threshold(self, threshold):
        self._queue.put(("threshold", threshold))

    # ---- public API: card side --------------------------------------------

    def show_card_status(self, message):
        self._queue.put(("card_status", message))

    def show_card_match(self, profile):
        self._queue.put(("card_match", profile))

    def show_card_failure(self, reason, reason_code=None):
        self._queue.put(("card_failure", (reason, reason_code)))

    def set_queue_count(self, count):
        self._queue.put(("queue_count", count))

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
                if kind == "recognitions":
                    recognitions, image_size = payload
                    self._render_recognitions(recognitions, image_size)
                elif kind == "offline":
                    self._render_offline(payload)
                elif kind == "seed":
                    entries, exits, unknown, spoof, occlusion = payload
                    self.stats["entries"], self.stats["exits"] = entries, exits
                    self.stats["unknown"], self.stats["spoof"] = unknown, spoof
                    self.stats["occlusion"] = occlusion
                    self._refresh_stat_labels()
                elif kind == "threshold":
                    self.threshold_label.configure(
                        text=self._pad(f"Recognition running · threshold {payload:.2f}")
                    )
                elif kind == "card_match":
                    self._render_card_match(payload)
                elif kind == "card_failure":
                    reason, reason_code = payload
                    self._render_card_failure(reason, reason_code)
                elif kind == "card_status":
                    self._show_card_waiting()
                elif kind == "queue_count":
                    self.queue_label.configure(text=self._pad(f"Queue {payload}"))
                elif kind == "backend_ok":
                    self.backend_label.configure(
                        text=self._pad("Backend ✓" if payload else "Backend ✗"),
                        text_color=SUCCESS if payload else DANGER,
                    )
                elif kind == "camera_ok":
                    self.camera_label.configure(
                        text=self._pad("Camera ✓" if payload else "Camera ✗"),
                        text_color=SUCCESS if payload else DANGER,
                    )
        except queue.Empty:
            pass
        self.window.after(100, self._process_queue)

    def _render_offline(self, offline):
        if offline:
            self.sync_label.configure(text=self._pad("Offline - retrying"), text_color=DANGER)
        else:
            self.sync_label.configure(text=self._pad("Synced"), text_color=SUCCESS)

    # ---- card scanner ---------------------------------------------------

    def _handle_card_input(self, _event):
        nfc_id = self._card_input_var.get().strip()
        self._card_input_var.set("")
        if not nfc_id:
            self.show_card_failure("Card read failed - try again.", "read_error")
            return
        if self.on_tap:
            self.on_tap(nfc_id)

    def _keep_focus(self):
        if self._closed:
            return
        # The reader is HID-keyboard-emulation, so this hidden field has to
        # stay focused for a tap to land anywhere at all. Nothing else in
        # this window takes typed input, so reclaiming focus can't interrupt
        # the guard - and mouse-wheel scrolling of the live log is bound to
        # the pointer, not focus, so the log still scrolls freely.
        if self.window.focus_get() is not self._card_input:
            self._card_input.focus_force()
        self.window.after(FOCUS_CHECK_MS, self._keep_focus)

    def _show_card_waiting(self):
        self.card_result.grid_forget()
        self.card_waiting.grid(row=0, column=0, sticky="nsew")

    def _show_card_result(self):
        self.card_waiting.grid_forget()
        self.card_result.grid(row=0, column=0, sticky="nsew")

    def _set_card_avatar(self, photo_bytes, name, fallback_color):
        avatar = _avatar_image(photo_bytes, self.CARD_AVATAR_SIZE)
        if avatar is None:
            avatar = _initials_avatar(
                name or "?", self.CARD_AVATAR_SIZE, bg=MAROON if name else fallback_color
            )
        self._card_avatar_image = ctk.CTkImage(
            light_image=avatar, dark_image=avatar, size=(self.CARD_AVATAR_SIZE, self.CARD_AVATAR_SIZE)
        )
        self.card_avatar.configure(image=self._card_avatar_image, text="")

    def _render_card_match(self, profile):
        timestamp = datetime.now().strftime("%I:%M:%S %p")
        name = profile.get("name") or "Unknown"
        direction = (profile.get("direction") or self.direction or "").upper()

        self._set_card_avatar(profile.get("photo_bytes"), name, SUCCESS)
        self.card_status.configure(text="✓ Access granted", text_color=SUCCESS)
        self.card_name.configure(text=name, text_color=TEXT_PRIMARY)
        role = (profile.get("role") or "").capitalize()
        self.card_sub.configure(
            text=" · ".join(part for part in (role, profile.get("department")) if part) or "—"
        )
        self.card_ids.configure(
            text=f"ID {profile.get('student_id') or '—'}   ·   CARD {profile.get('card_id') or '—'}"
        )
        self.card_ids.pack(fill="x", pady=(8, 0), before=self.card_time)
        note = profile.get("distinguishing_note")
        if note:
            self.card_note.configure(text=f"⚠ {note}")
            self.card_note.pack(fill="x", pady=(4, 0), before=self.card_time)
        else:
            self.card_note.pack_forget()
        self.card_time.configure(text=f"Logged · {timestamp} · {direction.capitalize()}")
        self._show_card_result()
        self._schedule_card_reset()

        self._push_log_row(
            name=name,
            meta=f"{profile.get('student_id') or '—'} · {timestamp}",
            badge_text=f"CARD · {direction}" if direction else "CARD",
            color=SUCCESS,
            photo_bytes=profile.get("photo_bytes"),
            initials_seed=name,
        )

    def _render_card_failure(self, reason, reason_code):
        timestamp = datetime.now().strftime("%I:%M:%S %p")
        label = self.FAILURE_HEADLINES.get(reason_code, "Access denied")

        self._set_card_avatar(None, None, DANGER)
        self.card_status.configure(text="✗ Card rejected", text_color=DANGER)
        self.card_name.configure(text=label, text_color=DANGER)
        self.card_sub.configure(text=reason or "")
        self.card_ids.pack_forget()
        self.card_note.pack_forget()
        self.card_time.configure(text=timestamp)
        self._show_card_result()
        self._schedule_card_reset()

        self._push_log_row(
            name=label,
            meta=f"Card rejected · {timestamp}",
            badge_text="CARD ✗",
            color=DANGER,
            photo_bytes=None,
            name_color=DANGER,
        )

    def _schedule_card_reset(self):
        if self._card_reset_job:
            self.window.after_cancel(self._card_reset_job)
        self._card_reset_job = self.window.after(RESET_DELAY_MS, self._reset_card)

    def _reset_card(self):
        self._card_reset_job = None
        if self._closed:
            return
        self._show_card_waiting()

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
                self._show_alert_banner("⚠  Possible spoof detected — photo/screen, not a live face")
                _play_alert_sound()
            elif item.get("occlusion_suspected"):
                self.stats["occlusion"] += 1
                # No alarm sound - unlike spoof/unknown, covering your face
                # isn't inherently adversarial (a scarf, a cough, a phone
                # call), so this is shown prominently but doesn't escalate
                # audibly by default.
                self._show_alert_banner("🤚  Please uncover your face and rescan")
            elif item["matched"]:
                key = "exits" if item["direction"] == "exit" else "entries"
                self.stats[key] += 1
            else:
                self.stats["unknown"] += 1
                self._show_alert_banner("⚠  Unknown person detected — verify identity")
                _play_alert_sound()
            self._push_log_entry(item)

        self._refresh_stat_labels()

    def _show_alert_banner(self, text):
        """A visible banner over the video feed plus an audible alarm - each
        fires once per genuinely new unmatched-face or spoof-suspected event
        (the same dedup the stats/log already rely on upstream in this
        method, keyed off log_id), not on every ~0.2s poll while the
        person/attempt is still in frame, so this can't turn into a
        continuous blare. Just sets state here - _draw_overlays (called every
        video refresh tick from _update_video, several times a second) is
        what actually draws it, the same as the "LIVE MONITOR" badge."""
        self._alert_text = text
        if self._alert_hide_job:
            self.window.after_cancel(self._alert_hide_job)
        self._alert_hide_job = self.window.after(self.ALERT_DISPLAY_MS, self._hide_alert_banner)

    def _hide_alert_banner(self):
        self._alert_hide_job = None
        self._alert_text = None
        # No explicit redraw needed - _update_video's own timer (running
        # continuously at VIDEO_REFRESH_MS regardless of camera state) picks
        # this up and simply stops drawing the banner on its next tick.

    def _refresh_stat_labels(self):
        self.stat_tiles["entries"].configure(text=str(self.stats["entries"]))
        self.stat_tiles["unknown"].configure(text=str(self.stats["unknown"]))
        self.stat_tiles["spoof"].configure(text=str(self.stats["spoof"]))
        self.stat_tiles["occlusion"].configure(text=str(self.stats["occlusion"]))
        self.stat_tiles["today"].configure(text=str(self.stats["entries"] + self.stats["exits"]))

    def _push_log_row(self, *, name, meta, badge_text, color, photo_bytes,
                      name_color=None, initials_seed=None):
        """The one way anything reaches the live log. Both credentials go
        through here - a face event from the scan loop and an NFC tap render
        as the same kind of card, differing only in their badge - so the log
        is a single chronological record of the gate rather than two
        half-stories. `color` carries the status (green/amber/red) directly
        instead of being re-derived per entry, since "denied card tap" and
        "unmatched face" are both failures but not the same color.
        `initials_seed` is the name to build a placeholder badge from when
        there's no photo; None falls back to a neutral "?"."""
        self._log_entries.insert(0, {
            "name": name,
            "meta": meta,
            "badge_text": badge_text,
            "color": color,
            "photo_bytes": photo_bytes,
            "name_color": name_color or TEXT_PRIMARY,
            "initials_seed": initials_seed,
        })
        self._log_entries = self._log_entries[: self.MAX_LOG_ROWS]
        self._rebuild_log_list()

    def _push_log_entry(self, item):
        timestamp = datetime.now().strftime("%I:%M:%S %p")
        # A moment of occlusion seen earlier in this same encounter, even
        # though it isn't this event's own outcome (that's occlusion_suspected
        # below, handled separately) - appended to whichever meta line already
        # fires, rather than dropped, per EntryLog.occlusion_detected.
        occlusion_note = " · face briefly covered" if item.get("occlusion_seen") else ""
        if item.get("spoof_suspected"):
            self._push_log_row(
                name="Possible spoof",
                meta=f"Liveness check failed · {timestamp}{occlusion_note}",
                badge_text="SPOOF", color=DANGER,
                photo_bytes=item.get("photo_bytes"), name_color=DANGER,
            )
        elif item.get("occlusion_suspected"):
            self._push_log_row(
                name="Please uncover your face",
                meta=f"Mouth/nose covered · {timestamp}",
                badge_text="COVERED", color=OCCLUSION,
                photo_bytes=item.get("photo_bytes"), name_color=OCCLUSION,
            )
        elif item["matched"]:
            self._push_log_row(
                name=item["name"],
                meta=f"{item.get('student_id') or '—'} · {timestamp}{occlusion_note}",
                badge_text=item["direction"].upper(), color=SUCCESS,
                photo_bytes=item.get("photo_bytes"), initials_seed=item["name"],
            )
        else:
            self._push_log_row(
                name="Unknown face",
                meta=f"Not matched · {timestamp}{occlusion_note}",
                badge_text="UNKNOWN", color=WARNING,
                photo_bytes=item.get("photo_bytes"), name_color=WARNING,
            )

    def _log_thumbnail(self, entry):
        """The reference photo for a match, or the actual cropped capture
        for an unrecognized/spoof-suspected face - so the guard sees who or
        what the system caught, not just a name. Falls back to an
        initials/? badge when no photo is available (fetch failed, nothing
        was captured, or the tap was rejected outright)."""
        avatar = _avatar_image(entry["photo_bytes"], self.LOG_THUMB_SIZE)
        if avatar is None:
            seed = entry["initials_seed"]
            avatar = _initials_avatar(
                seed or "?", self.LOG_THUMB_SIZE, bg=MAROON if seed else entry["color"]
            )
        return ctk.CTkImage(light_image=avatar, dark_image=avatar, size=(self.LOG_THUMB_SIZE, self.LOG_THUMB_SIZE))

    def _rebuild_log_list(self):
        count = len(self._log_entries)
        self.log_count_label.configure(text=self._pad(f"{count} event{'s' if count != 1 else ''}"))

        for child in self.log_list.winfo_children():
            child.destroy()
        if not self._log_entries:
            ctk.CTkLabel(
                self.log_list, text="Waiting for the first scan or tap...",
                font=(FONT, 11), text_color=TEXT_MUTED,
            ).grid(row=0, column=0, columnspan=self.LOG_GRID_COLUMNS, pady=16)
            return
        self._log_photo_images = []  # keep CTkImage refs alive - Tk drops unreferenced ones
        for index, entry in enumerate(self._log_entries):
            row, col = divmod(index, self.LOG_GRID_COLUMNS)
            status_color = entry["color"]
            card = ctk.CTkFrame(
                self.log_list, fg_color=CARD_BG, corner_radius=14,
                border_width=2, border_color=status_color,
            )
            card.grid(row=row, column=col, sticky="nsew", padx=6, pady=6)

            ctk.CTkLabel(
                card, text=entry["badge_text"], font=(FONT, 9, "bold"), text_color="white",
                fg_color=status_color, corner_radius=8,
            ).pack(pady=(10, 8), ipadx=8, ipady=2)

            thumb_image = self._log_thumbnail(entry)
            self._log_photo_images.append(thumb_image)
            thumb_wrap = ctk.CTkFrame(
                card, fg_color="transparent", corner_radius=12,
                border_width=2, border_color=status_color,
            )
            thumb_wrap.pack(pady=(0, 8), padx=10)
            ctk.CTkLabel(thumb_wrap, image=thumb_image, text="").pack(padx=3, pady=3)

            ctk.CTkLabel(
                card, text=entry["name"], font=(FONT, 13, "bold"), text_color=entry["name_color"],
                wraplength=170, justify="center",
            ).pack(padx=10)
            ctk.CTkLabel(
                card, text=entry["meta"], font=(FONT, 9), text_color=TEXT_MUTED,
                wraplength=170, justify="center",
            ).pack(padx=10, pady=(2, 14))

    def _update_video(self):
        if self._closed:
            return
        frame = self._get_preview_frame()
        if frame is not None:
            self._last_frame_at = time.monotonic()
            self._draw_frame(frame)
        elif time.monotonic() - self._last_frame_at >= self.CAMERA_GRACE_SECONDS:
            # No frame for a while now - either no camera ever connected, or
            # one did and then stopped (unplugged, driver crash - see
            # Camera._close_after_failure). Either way this keeps checking:
            # Camera's own background loop keeps retrying the connection on
            # its own timer for as long as the app runs, so the moment a
            # frame actually arrives again, the branch above takes back over
            # and this placeholder stops being drawn - no restart needed.
            self._draw_no_camera_placeholder()
        self.window.after(VIDEO_REFRESH_MS, self._update_video)

    def _panel_size(self):
        return (
            self.video_canvas.winfo_width() or self.MIN_VIDEO_SIZE[0],
            self.video_canvas.winfo_height() or self.MIN_VIDEO_SIZE[1],
        )

    def _draw_rounded_label(self, x0, y0, x1, y1, text, *, fill, text_color="white", font_size=10, bold=True):
        """One shared "rounded, alpha-composited pill with centered text"
        drawer for every canvas overlay that needs one - the "LIVE MONITOR"
        badge and the alert banner both go through this now, specifically so
        this class of bug (a rounded shape whose corner cutouts get painted
        some flat color instead of showing the live video through them -
        see _draw_overlays and _show_alert_banner's comments for the two
        real instances of it found here) has exactly one correct
        implementation to share, rather than each overlay growing its own
        copy that could independently regress. Draws straight onto
        self.video_canvas - true transparency, not an image composited in
        afterwards, so there's no separate mask/alpha step to get wrong: the
        canvas polygon simply doesn't cover the corner pixels at all, and
        whatever was drawn there already (the live frame) just shows
        through, with the arcs themselves genuinely anti-aliased by Tk's own
        polygon rendering (see _rounded_rect_points for why these are true
        trigonometric arcs, not a spline-smoothed approximation)."""
        weight = "bold" if bold else "normal"
        self.video_canvas.create_polygon(
            # radius = half the box height, same as the badge always used -
            # the shortest side of a pill sets how round it can go before
            # the two end-caps would overlap.
            _rounded_rect_points(x0, y0, x1, y1, radius=(y1 - y0) / 2),
            # smooth=False - the points already trace true quarter-circle
            # arcs (see _rounded_rect_points), so Tk's spline smoothing
            # would only soften the genuinely round shape back down again.
            fill=fill, outline="", smooth=False,
        )
        self.video_canvas.create_text(
            (x0 + x1) / 2, (y0 + y1) / 2, text=text, font=(FONT, font_size, weight), fill=text_color,
        )

    def _draw_overlays(self, canvas_w, canvas_h):
        """The "LIVE MONITOR" badge, the status caption, and the alert
        banner (if one is currently showing) - drawn straight onto the
        canvas, on top of whatever was just drawn there (a live frame, the
        "no camera" placeholder, and any recognition boxes), so each is the
        only thing visible where it sits - no separate widget, no
        surrounding rectangle beyond the rounded pill itself. See
        _build_video_panel's comment for why this replaced CTkLabel widgets.
        Called last from both _draw_frame and _draw_no_camera_placeholder,
        which between them run continuously at VIDEO_REFRESH_MS regardless
        of camera state - that's what makes the alert banner's appear/
        disappear timing (driven by _show_alert_banner/_hide_alert_banner
        just flipping self._alert_text) actually visible without a redraw
        call of its own."""
        badge_x, badge_y = canvas_w * 0.015, canvas_h * 0.022
        badge_w, badge_h = 118, 24
        self._draw_rounded_label(
            badge_x, badge_y, badge_x + badge_w, badge_y + badge_h,
            "● LIVE MONITOR", fill=DANGER,
        )

        # No backing rectangle at all here - a plain text draw, so there is
        # nothing to blend with anything: it just floats over the feed.
        self.video_canvas.create_text(
            canvas_w * 0.015, canvas_h * 0.975, text=self._caption_text,
            font=(FONT, 10), fill="#9CA3AF", anchor="sw",
        )

        if self._alert_text:
            # Width comes from actual font metrics, not a guessed character
            # count - this draws at most a few times a second (once per
            # video refresh tick, never per recognition item), so measuring
            # properly costs nothing worth avoiding and gets the pill's
            # edges right around the real text instead of over/under-sized.
            banner_height = 36
            banner_font = tkfont.Font(family=FONT, size=13, weight="bold")
            banner_width = banner_font.measure(self._alert_text) + 2 * 18
            banner_cx = canvas_w * 0.5
            # Sits below the LIVE pill's row rather than level with it, so a
            # long banner can't slide under the pill on a narrow window -
            # same 10%-down position the old CTkLabel used (rely=0.10).
            banner_top = canvas_h * 0.10
            self._draw_rounded_label(
                banner_cx - banner_width / 2, banner_top,
                banner_cx + banner_width / 2, banner_top + banner_height,
                self._alert_text, fill=DANGER, font_size=13,
            )

    def _draw_no_camera_placeholder(self):
        canvas_w, canvas_h = self._panel_size()
        panel = _round_corners(
            Image.new("RGBA", (canvas_w, canvas_h), VIDEO_BG), self.VIDEO_PANEL_CORNER_RADIUS
        )
        # Kept as self._video_image (not a local) for the same reason
        # _draw_frame does - Tk drops a PhotoImage with no surviving
        # reference, which would blank the canvas on the very next redraw.
        self._video_image = ImageTk.PhotoImage(panel)
        self.video_canvas.delete("all")
        self.video_canvas.create_image(0, 0, image=self._video_image, anchor="nw")

        cx, cy = canvas_w / 2, canvas_h / 2
        self.video_canvas.create_text(
            cx, cy - 20, text="📷", font=(FONT, 32), fill=TEXT_MUTED,
        )
        self.video_canvas.create_text(
            cx, cy + 14, text="No camera connected", font=(FONT, 14, "bold"), fill=TEXT_MUTED,
        )
        self.video_canvas.create_text(
            cx, cy + 36, text="Card taps still work - connecting a camera will resume automatically",
            font=(FONT, 10), fill=TEXT_MUTED,
        )
        self._caption_text = "No camera detected"
        self._draw_overlays(canvas_w, canvas_h)

    def _draw_frame(self, frame):
        rgb = frame[:, :, ::-1]  # BGR (OpenCV) -> RGB
        image = Image.fromarray(rgb)

        canvas_w, canvas_h = self._panel_size()
        target = _scaled_size(image.size, (canvas_w, canvas_h))
        image = image.resize(target, Image.LANCZOS)

        img_w, img_h = target
        origin_x, origin_y = (canvas_w - img_w) // 2, (canvas_h - img_h) // 2
        # One full-panel-sized composite - a solid VIDEO_BG rectangle (cheap;
        # no rounding drawn on it yet) with the live frame pasted at its
        # centered offset, letterboxed on whichever sides don't match the
        # panel's aspect ratio - then ONE rounded-corner mask over the whole
        # thing. Masking after the paste (not the frame alone, before
        # pasting) is what actually fixes the black-square-corner bug: if the
        # live frame's own size happens to leave little or no letterbox, a
        # mask applied only to it beforehand wouldn't reach the panel's true
        # corners at all - this way the final clip always matches the panel
        # exactly, regardless of the frame's aspect ratio.
        panel = Image.new("RGBA", (canvas_w, canvas_h), VIDEO_BG)
        panel.paste(image, (origin_x, origin_y))
        panel = _round_corners(panel, self.VIDEO_PANEL_CORNER_RADIUS)
        self._video_image = ImageTk.PhotoImage(panel)

        self.video_canvas.delete("all")
        self.video_canvas.create_image(0, 0, image=self._video_image, anchor="nw")

        src_w, src_h = self._latest_image_size
        if src_w > 0 and src_h > 0:
            for item in self._latest_recognitions:
                self._draw_box(item, origin_x, origin_y, img_w, img_h, src_w, src_h)

        count = len(self._latest_recognitions)
        self._caption_text = f"{src_w}×{src_h} · {count} face{'s' if count != 1 else ''} tracked"
        self._draw_overlays(canvas_w, canvas_h)

    def _draw_box(self, item, origin_x, origin_y, img_w, img_h, src_w, src_h):
        box = item.get("box")
        if not box:
            return
        x0 = origin_x + (box["left"] / src_w) * img_w
        y0 = origin_y + (box["top"] / src_h) * img_h
        x1 = origin_x + (box["right"] / src_w) * img_w
        y1 = origin_y + (box["bottom"] / src_h) * img_h

        if item.get("spoof_suspected"):
            # Liveness (anti-spoofing) is flagging this face - shown red as
            # soon as THIS frame's score misses the threshold, not only once
            # the multi-frame vote confirms it (see IdentifyView.
            # _confirm_or_vote_spoof) - a guard should see the warning the
            # moment it's suspected. Still just a suspicion, not yet logged/
            # alarmed, while item["retry"] is also true (see _render_
            # recognitions) - the label makes that distinction visible too.
            color = DANGER
            label = "⚠ Possible spoof" if not item.get("retry") else "⚠ Checking - possible spoof"
        elif item.get("occlusion_suspected"):
            # Also its own branch, checked before the generic retry case
            # below for the same reason spoof is: it needs its own color
            # (OCCLUSION, not the generic checking-gray retry uses) even
            # while unconfirmed. Unlike spoof, the label doesn't hedge
            # between "checking" and "confirmed" - telling someone to
            # uncover their face is a harmless thing to say even if this
            # frame's read turns out wrong, so there's no reason to soften it
            # the way a spoof accusation needs softening.
            color = OCCLUSION
            label = "🤚 Please uncover your face"
        elif item.get("retry"):
            # Not a decided outcome yet - a skipped frame (blurry,
            # edge-cropped, or the face turned away from the camera), or an
            # unmatched face still short of enough agreement to count as a
            # real "Unknown" (see IdentifyView). Neutral color so this never
            # reads as a red flag on a single bad frame. The backend sends a
            # short hint for the skips a person can actually act on ("Face
            # the camera"); the voting paths have nothing to act on and fall
            # back to the generic label.
            color = TEXT_MUTED
            label = item.get("hint") or "Checking..."
        elif item.get("tiebreak"):
            color = ACCENT
            # A confusable-pair tiebreak means the match itself looked fine -
            # it's being overridden anyway because this person is on record
            # as easily confused with someone similar (see users.models.
            # ConfusablePair) - worth its own label so it doesn't read as an
            # ordinary "the system wasn't sure" prompt.
            label = "Tap card - lookalike check" if item.get("confusable_pair") else "Tap card to confirm"
        elif item["matched"]:
            color = SUCCESS
            label = item["name"]
            if item.get("confidence") is not None:
                label = f"{label} · {item['confidence']}%"
        else:
            color = WARNING
            label = "Unknown"

        self.video_canvas.create_rectangle(x0, y0, x1, y1, outline=color, width=2)
        label_y = max(y0 - 10, 10)
        label_w = max(60, len(label) * 6.5)
        self.video_canvas.create_rectangle(x0, label_y - 9, x0 + label_w, label_y + 9, fill=color, outline="")
        self.video_canvas.create_text(
            x0 + 4, label_y, text=label, anchor="w", font=(FONT, 9, "bold"),
            fill="black" if color in (WARNING, TEXT_MUTED) else "white",
        )

    def _handle_close(self):
        self._closed = True
        if self._on_close:
            self._on_close()
        self.window.destroy()
