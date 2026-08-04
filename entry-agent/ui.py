import io
import os
import queue
import threading
import tkinter as tk
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
PLACEHOLDER_AVATAR = "#D1D5DB"

FONT = "Segoe UI"
FONT_MONO = "Consolas"

FOCUS_CHECK_MS = 300
RESET_DELAY_MS = 8000
VIDEO_REFRESH_MS = 42  # ~24 fps

ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
ICON_PATH = os.path.join(ASSETS_DIR, "icon.png")

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------


def _apply_icon(window):
    """CustomTkinter windows still use Tk's iconphoto under the hood - a PNG
    works directly there (unlike iconbitmap, which specifically wants a
    Windows .ico). Non-fatal if the asset is missing - falls back to the
    default Tk icon rather than crashing the window."""
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


class StatusPill(ctk.CTkLabel):
    """A small check/x status indicator, e.g. "✓ Camera" / "✗ Camera"."""

    def __init__(self, parent, label):
        # NOTE: don't name this attribute self._label - CTkLabel already
        # uses that internally for its own private Tk Label widget, and
        # overwriting it here silently breaks configure() on this instance.
        super().__init__(parent, text=f"✓ {label}", font=(FONT, 11), text_color=SUCCESS)
        self._pill_label = label

    def set_ok(self, ok):
        if ok:
            self.configure(text=f"✓ {self._pill_label}", text_color=SUCCESS)
        else:
            self.configure(text=f"✗ {self._pill_label}", text_color=DANGER)


class ResultCard(ctk.CTkFrame):
    """photo, name, ID, course, card ID, direction + timestamp - the shared
    result display for a successful card scan, per the design spec's
    "shared components, build once" section."""

    def __init__(self, parent, avatar_size=140):
        super().__init__(parent, fg_color=CARD_BG, corner_radius=16, border_width=1, border_color=BORDER)
        self.avatar_size = avatar_size
        self._avatar_image = None

        inner = ctk.CTkFrame(self, fg_color="transparent")
        inner.pack(padx=32, pady=26)

        self.avatar_label = ctk.CTkLabel(inner, text="")
        self.avatar_label.pack(pady=(0, 14))

        self.name_label = ctk.CTkLabel(inner, text="", font=(FONT, 18, "bold"), text_color=TEXT_PRIMARY)
        self.name_label.pack()
        self.course_label = ctk.CTkLabel(inner, text="", font=(FONT, 12), text_color=TEXT_SECONDARY)
        self.course_label.pack(pady=(2, 14))

        details = ctk.CTkFrame(inner, fg_color="transparent")
        details.pack(fill="x")
        self.id_value = self._detail_row(details, "ID ·")
        self.card_value = self._detail_row(details, "Card ID ·")

        self.logged_label = ctk.CTkLabel(inner, text="", font=(FONT, 10), text_color=TEXT_MUTED)
        self.logged_label.pack(pady=(12, 0))

    def _detail_row(self, parent, prefix):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=2)
        ctk.CTkLabel(row, text=prefix, font=(FONT, 11), text_color=TEXT_MUTED).pack(side="left")
        value = ctk.CTkLabel(row, text="", font=(FONT_MONO, 12), text_color=TEXT_PRIMARY)
        value.pack(side="left", padx=(6, 0))
        return value

    def update_result(self, *, photo_bytes, name, role, course, student_id, card_id, direction, timestamp):
        avatar = _avatar_image(photo_bytes, self.avatar_size)
        if avatar is None:
            avatar = _initials_avatar(name, self.avatar_size)
        # CTkImage (not a raw ImageTk.PhotoImage) so CTkLabel scales it
        # correctly on HiDPI displays - the video canvas and window icon
        # elsewhere in this file are plain tk widgets, not CTk ones, so they
        # still need a plain PhotoImage instead.
        self._avatar_image = ctk.CTkImage(
            light_image=avatar, dark_image=avatar, size=(self.avatar_size, self.avatar_size)
        )
        self.avatar_label.configure(image=self._avatar_image, text="")

        self.name_label.configure(text=name or "Unknown")
        role_text = (role or "").capitalize()
        self.course_label.configure(text=" · ".join(part for part in (role_text, course) if part) or "—")
        self.id_value.configure(text=student_id or "—")
        self.card_value.configure(text=card_id or "—")
        direction_text = (direction or "").capitalize()
        self.logged_label.configure(text=f"Logged · {timestamp} · {direction_text}")


# ---------------------------------------------------------------------------
# Window 1 - Launcher
# ---------------------------------------------------------------------------


class MenuWindow:
    """The first thing that opens when entry-agent starts: a small launcher
    that lets the guard choose which scanner to open. Both the camera
    scanner and the card scanner can be open at the same time - this window
    just starts them, it doesn't own or replace them. Closing this window
    exits the whole app; closing a scanner window just returns control here.
    """

    def __init__(self, gate_location, officer_name, version, on_open_camera, on_open_card, on_exit):
        self._hover_animators = []
        self.root = ctk.CTk()
        self.root.title("EVSU SecureTap")
        self.root.configure(fg_color=BG)
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", on_exit)
        _apply_icon(self.root)

        self.header = HeaderBar(
            self.root, "EVSU SecureTap", f"{gate_location} · choose a scanner", center=True, logo_path=ICON_PATH
        )
        self.header.pack(fill="x")

        status_wrap = ctk.CTkFrame(self.root, fg_color="transparent")
        status_wrap.pack(fill="x")
        status_row = ctk.CTkFrame(status_wrap, fg_color="transparent")
        status_row.pack(pady=16)
        self.status_pills = {}
        for key, label in (("reader", "NFC reader"), ("camera", "Camera"), ("server", "Server")):
            pill = StatusPill(status_row, label)
            pill.pack(side="left", padx=18)
            self.status_pills[key] = pill
        ctk.CTkFrame(self.root, fg_color=BORDER, height=1).pack(fill="x")

        buttons = ctk.CTkFrame(self.root, fg_color="transparent")
        buttons.pack(fill="x", padx=28, pady=24)
        self._scanner_button(buttons, "Camera scanner", "Face recognition entry", on_open_camera).pack(
            fill="x", pady=(0, 16)
        )
        self._scanner_button(buttons, "Card scanner", "Tap NFC ID card", on_open_card).pack(fill="x")

        ctk.CTkFrame(self.root, fg_color=BORDER, height=1).pack(fill="x")
        footer = ctk.CTkFrame(self.root, fg_color="transparent")
        footer.pack(fill="x", padx=28, pady=16)
        ctk.CTkLabel(footer, text=officer_name, font=(FONT, 11), text_color=TEXT_MUTED).pack(side="left")
        self._version = version
        self.footer_clock = ctk.CTkLabel(footer, text="", font=(FONT, 11), text_color=TEXT_MUTED)
        self.footer_clock.pack(side="right")
        self._tick_footer_clock()

    def _scanner_button(self, parent, title, subtitle, command):
        """A clickable maroon card with a bold title + lighter subtitle on
        their own lines - built from a plain CTkFrame with a click binding
        rather than CTkButton, since CTkButton's single `text` option can't
        render two different font weights/colors the way the design
        reference does. Animates to a darker maroon on hover (see
        _HoverAnimator) and shows a pointing-hand cursor, so it reads as
        clickable rather than just a colored label."""
        card = ctk.CTkFrame(parent, fg_color=MAROON, corner_radius=16, cursor="hand2")
        inner = ctk.CTkFrame(card, fg_color="transparent", cursor="hand2")
        inner.pack(fill="both", expand=True, padx=24, pady=18)
        title_label = ctk.CTkLabel(
            inner, text=title, font=(FONT, 17, "bold"), text_color="white", anchor="w", cursor="hand2"
        )
        title_label.pack(fill="x")
        subtitle_label = ctk.CTkLabel(
            inner, text=subtitle, font=(FONT, 12), text_color=MAROON_LIGHT, anchor="w", cursor="hand2"
        )
        subtitle_label.pack(fill="x", pady=(3, 0))

        animator = _HoverAnimator(card, MAROON, MAROON_DARK)
        self._hover_animators.append(animator)  # keep a reference alive

        def handle_click(_event=None):
            command()

        for widget in (card, inner, title_label, subtitle_label):
            widget.bind("<Button-1>", handle_click)
            widget.bind("<Enter>", animator.enter)
            widget.bind("<Leave>", animator.leave)
        return card

    def set_status(self, key, ok):
        if key in self.status_pills:
            self.status_pills[key].set_ok(ok)

    def _tick_footer_clock(self):
        if not self.root.winfo_exists():
            return
        self.footer_clock.configure(text=f"{datetime.now().strftime('%I:%M %p')} · {self._version}")
        self.root.after(1000, self._tick_footer_clock)

    def run_forever(self):
        self.root.mainloop()


# ---------------------------------------------------------------------------
# Window 2 - Camera scanner (CCTV monitoring mode)
# ---------------------------------------------------------------------------


class FeedbackWindow:
    """The camera scanner: a CCTV-style monitoring view, not a one-person
    kiosk. The camera continuously watches a stream of people walking
    through - nobody stops or poses, and several faces can be recognized in
    the same frame. Every bounding box, name, and confidence percentage
    drawn on the feed comes straight from the backend's /api/identify
    response for that exact frame (via show_recognitions()), not a separate
    local detector - one source of truth for "is this a face" and "who is
    it", so the box position and the identity label can never disagree.

    Per-person duplicate suppression already happens server-side (a
    recognized person or a lingering unrecognized face reuses its existing
    log row within a cooldown window, see backend/logs/views.py) - this
    window just has to *notice* that via the `deduped` flag on each result
    and only count/log genuinely new events, or the live log and stats
    strip would restate the same person every ~0.2s scan cycle.
    """

    MIN_VIDEO_SIZE = (320, 240)
    MAX_LOG_ROWS = 60
    LOG_GRID_COLUMNS = 2
    ALERT_DISPLAY_MS = 6000

    def __init__(self, parent, gate_location, direction, get_preview_frame, on_close=None):
        self._get_preview_frame = get_preview_frame
        self.gate_location = gate_location
        self.direction = direction
        self._on_close = on_close
        self._closed = False

        self._video_image = None
        self._latest_recognitions = []
        self._latest_image_size = (1, 1)
        self._seen_log_ids = set()
        self._log_entries = []  # newest first
        self._log_photo_images = []  # keeps CTkImage refs alive for the log list
        self.stats = {"entries": 0, "exits": 0, "unknown": 0}
        self._alert_hide_job = None

        self.window = ctk.CTkToplevel(parent)
        self.window.title(f"EVSU SecureTap - Camera scanner - {gate_location}")
        self.window.configure(fg_color=BG)
        self.window.geometry("1280x780")
        self.window.minsize(920, 620)
        self.window.protocol("WM_DELETE_WINDOW", self._handle_close)
        self.window.bind("<Escape>", lambda _e: self._handle_close())
        _apply_icon(self.window)

        self.window.grid_rowconfigure(2, weight=1)
        self.window.grid_columnconfigure(0, weight=1)

        self.header = HeaderBar(self.window, f"{gate_location} — live monitoring", show_clock=True)
        self.header.grid(row=0, column=0, sticky="ew")

        self._build_stats_strip()
        self._build_main_area()
        self._build_status_bar()
        self._rebuild_log_list()

        self._queue = queue.Queue()
        self.window.after(100, self._process_queue)
        if self._get_preview_frame:
            self.window.after(VIDEO_REFRESH_MS, self._update_video)

    # ---- layout ---------------------------------------------------------

    def _build_stats_strip(self):
        strip = ctk.CTkFrame(self.window, fg_color="transparent")
        strip.grid(row=1, column=0, sticky="ew", padx=16, pady=(14, 6))

        specs = [
            ("today", "Today", TEXT_PRIMARY),
            ("entries", "Entries", TEXT_PRIMARY),
            ("exits", "Exits", TEXT_PRIMARY),
            ("unknown", "Unknown", WARNING),
            ("in_frame", "In frame", TEXT_PRIMARY),
        ]
        self.stat_tiles = {}
        for i, (key, label, color) in enumerate(specs):
            strip.grid_columnconfigure(i, weight=1)
            tile = ctk.CTkFrame(strip, fg_color=CARD_BG, corner_radius=12, border_width=1, border_color=BORDER)
            tile.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 8, 0))
            ctk.CTkLabel(tile, text=label, font=(FONT, 10), text_color=TEXT_MUTED).pack(
                padx=14, pady=(10, 0), anchor="w"
            )
            value_label = ctk.CTkLabel(tile, text="0", font=(FONT, 22, "bold"), text_color=color)
            value_label.pack(padx=14, pady=(0, 10), anchor="w")
            self.stat_tiles[key] = value_label

    def _build_main_area(self):
        area = ctk.CTkFrame(self.window, fg_color="transparent")
        area.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 8))
        area.grid_rowconfigure(0, weight=1)
        area.grid_columnconfigure(0, weight=1)
        area.grid_columnconfigure(1, weight=2)

        video_frame = ctk.CTkFrame(area, fg_color="#111827", corner_radius=12)
        video_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        video_frame.grid_rowconfigure(0, weight=1)
        video_frame.grid_columnconfigure(0, weight=1)
        self.video_canvas = tk.Canvas(video_frame, bg="#111827", highlightthickness=0)
        self.video_canvas.grid(row=0, column=0, sticky="nsew", padx=2, pady=2)
        self.caption_label = ctk.CTkLabel(
            video_frame, text="Starting camera...", font=(FONT, 10), text_color="#9CA3AF", fg_color="#111827"
        )
        self.caption_label.place(relx=0.02, rely=0.97, anchor="sw")
        self.alert_banner = ctk.CTkLabel(
            video_frame, text="", font=(FONT, 13, "bold"), text_color="white",
            fg_color=DANGER, corner_radius=10,
        )

        log_frame = ctk.CTkFrame(area, fg_color=CARD_BG, corner_radius=12, border_width=1, border_color=BORDER)
        log_frame.grid(row=0, column=1, sticky="nsew")
        log_frame.grid_rowconfigure(1, weight=1)
        log_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(log_frame, text="Live log", font=(FONT, 13, "bold"), text_color=TEXT_PRIMARY).grid(
            row=0, column=0, sticky="w", padx=14, pady=(12, 4)
        )
        self.log_list = ctk.CTkScrollableFrame(log_frame, fg_color="transparent")
        self.log_list.grid(row=1, column=0, sticky="nsew", padx=6, pady=(0, 10))
        for col in range(self.LOG_GRID_COLUMNS):
            self.log_list.grid_columnconfigure(col, weight=1, uniform="log_card")

    def _build_status_bar(self):
        bar = ctk.CTkFrame(self.window, fg_color="transparent")
        bar.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 12))
        self.threshold_label = ctk.CTkLabel(bar, text="Recognition running", font=(FONT, 10), text_color=TEXT_MUTED)
        self.threshold_label.pack(side="left")
        self.sync_label = ctk.CTkLabel(bar, text="Synced", font=(FONT, 10), text_color=SUCCESS)
        self.sync_label.pack(side="right")

    # ---- public API -------------------------------------------------------

    def show_recognitions(self, recognitions, image_size):
        """recognitions: list of dicts with box/matched/name/student_id/
        department/confidence/log_id/deduped, one per face in the latest
        scanned frame. image_size: (width, height) of that frame, so boxes
        (in that frame's pixel coordinates) can be scaled onto the preview."""
        self._queue.put(("recognitions", (recognitions, image_size)))

    def show_offline(self, offline):
        self._queue.put(("offline", offline))

    def seed_stats(self, entries_today, exits_today, unknown_today):
        self._queue.put(("seed", (entries_today, exits_today, unknown_today)))

    def set_threshold(self, threshold):
        self._queue.put(("threshold", threshold))

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
                    entries, exits, unknown = payload
                    self.stats["entries"], self.stats["exits"], self.stats["unknown"] = entries, exits, unknown
                    self._refresh_stat_labels()
                elif kind == "threshold":
                    self.threshold_label.configure(text=f"Recognition running · threshold {payload:.2f}")
        except queue.Empty:
            pass
        self.window.after(100, self._process_queue)

    def _render_offline(self, offline):
        if offline:
            self.sync_label.configure(text="Offline - retrying", text_color=DANGER)
        else:
            self.sync_label.configure(text="Synced", text_color=SUCCESS)

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
            if item["matched"]:
                key = "exits" if item["direction"] == "exit" else "entries"
                self.stats[key] += 1
            else:
                self.stats["unknown"] += 1
                self._show_unknown_alert()
                _play_alert_sound()
            self._push_log_entry(item)

        self._refresh_stat_labels()

    def _show_unknown_alert(self):
        """A visible banner over the video feed plus an audible alarm - each
        fires once per genuinely new unmatched-face event (the same dedup
        the stats/log already rely on upstream in this method, keyed off
        log_id), not on every ~0.2s poll while the person is still in
        frame, so this can't turn into a continuous blare."""
        self.alert_banner.configure(text="⚠  Unknown person detected — verify identity")
        self.alert_banner.place(relx=0.5, rely=0.04, anchor="n")
        if self._alert_hide_job:
            self.window.after_cancel(self._alert_hide_job)
        self._alert_hide_job = self.window.after(self.ALERT_DISPLAY_MS, self._hide_unknown_alert)

    def _hide_unknown_alert(self):
        self._alert_hide_job = None
        if self._closed:
            return
        self.alert_banner.place_forget()

    def _refresh_stat_labels(self):
        self.stat_tiles["entries"].configure(text=str(self.stats["entries"]))
        self.stat_tiles["exits"].configure(text=str(self.stats["exits"]))
        self.stat_tiles["unknown"].configure(text=str(self.stats["unknown"]))
        self.stat_tiles["today"].configure(text=str(self.stats["entries"] + self.stats["exits"]))

    LOG_THUMB_SIZE = 64

    def _push_log_entry(self, item):
        timestamp = datetime.now().strftime("%I:%M:%S %p")
        if item["matched"]:
            meta = f"{item.get('student_id') or '—'} · {item['direction'].capitalize()} · {timestamp}"
        else:
            meta = f"not matched · flagged · {timestamp}"
        self._log_entries.insert(0, {
            "name": item["name"] if item["matched"] else "Unknown face",
            "matched": item["matched"],
            "meta": meta,
            "photo_bytes": item.get("photo_bytes"),
        })
        self._log_entries = self._log_entries[: self.MAX_LOG_ROWS]
        self._rebuild_log_list()

    def _log_thumbnail(self, entry):
        """The reference photo for a match, or the actual cropped capture
        for an unrecognized face - so the guard sees who the system thinks
        this is, not just a name. Falls back to an initials/? badge when no
        photo is available (fetch failed, or nothing was captured)."""
        avatar = _avatar_image(entry["photo_bytes"], self.LOG_THUMB_SIZE)
        if avatar is None:
            badge_text = entry["name"] if entry["matched"] else "?"
            avatar = _initials_avatar(badge_text, self.LOG_THUMB_SIZE, bg=MAROON if entry["matched"] else WARNING)
        return ctk.CTkImage(light_image=avatar, dark_image=avatar, size=(self.LOG_THUMB_SIZE, self.LOG_THUMB_SIZE))

    def _rebuild_log_list(self):
        for child in self.log_list.winfo_children():
            child.destroy()
        if not self._log_entries:
            ctk.CTkLabel(
                self.log_list, text="Waiting for the first scan...", font=(FONT, 11), text_color=TEXT_MUTED
            ).grid(row=0, column=0, columnspan=self.LOG_GRID_COLUMNS, pady=12)
            return
        self._log_photo_images = []  # keep CTkImage refs alive - Tk drops unreferenced ones
        for index, entry in enumerate(self._log_entries):
            row, col = divmod(index, self.LOG_GRID_COLUMNS)
            card = ctk.CTkFrame(self.log_list, fg_color=BG, corner_radius=10)
            card.grid(row=row, column=col, sticky="nsew", padx=5, pady=5)

            thumb_image = self._log_thumbnail(entry)
            self._log_photo_images.append(thumb_image)
            ctk.CTkLabel(card, image=thumb_image, text="").pack(pady=(12, 6))

            name_color = TEXT_PRIMARY if entry["matched"] else WARNING
            ctk.CTkLabel(
                card, text=entry["name"], font=(FONT, 12, "bold"), text_color=name_color,
                wraplength=120, justify="center",
            ).pack(padx=8)
            ctk.CTkLabel(
                card, text=entry["meta"], font=(FONT, 9), text_color=TEXT_MUTED,
                wraplength=120, justify="center",
            ).pack(padx=8, pady=(2, 12))

    def _update_video(self):
        if self._closed:
            return
        frame = self._get_preview_frame()
        if frame is not None:
            self._draw_frame(frame)
        self.window.after(VIDEO_REFRESH_MS, self._update_video)

    def _draw_frame(self, frame):
        rgb = frame[:, :, ::-1]  # BGR (OpenCV) -> RGB
        image = Image.fromarray(rgb)

        canvas_w = self.video_canvas.winfo_width() or self.MIN_VIDEO_SIZE[0]
        canvas_h = self.video_canvas.winfo_height() or self.MIN_VIDEO_SIZE[1]
        target = _scaled_size(
            image.size, (max(canvas_w - 4, self.MIN_VIDEO_SIZE[0]), max(canvas_h - 4, self.MIN_VIDEO_SIZE[1]))
        )
        image = image.resize(target, Image.LANCZOS)
        self._video_image = ImageTk.PhotoImage(image)

        self.video_canvas.delete("all")
        cx, cy = canvas_w / 2, canvas_h / 2
        self.video_canvas.create_image(cx, cy, image=self._video_image, anchor="center")

        img_w, img_h = target
        origin_x, origin_y = cx - img_w / 2, cy - img_h / 2
        src_w, src_h = self._latest_image_size
        if src_w > 0 and src_h > 0:
            for item in self._latest_recognitions:
                self._draw_box(item, origin_x, origin_y, img_w, img_h, src_w, src_h)

        count = len(self._latest_recognitions)
        self.caption_label.configure(
            text=f"{src_w}×{src_h} · {count} face{'s' if count != 1 else ''} tracked"
        )

    def _draw_box(self, item, origin_x, origin_y, img_w, img_h, src_w, src_h):
        box = item.get("box")
        if not box:
            return
        x0 = origin_x + (box["left"] / src_w) * img_w
        y0 = origin_y + (box["top"] / src_h) * img_h
        x1 = origin_x + (box["right"] / src_w) * img_w
        y1 = origin_y + (box["bottom"] / src_h) * img_h

        if item.get("retry"):
            # Not a decided outcome yet - a skipped blurry/edge-cropped
            # frame, or an unmatched face still short of enough agreement
            # to count as a real "Unknown" (see IdentifyView). Neutral
            # color so this never reads as a red flag on a single bad frame.
            color = TEXT_MUTED
            label = "Checking..."
        elif item.get("tiebreak"):
            color = ACCENT
            label = "Tap card to confirm"
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


# ---------------------------------------------------------------------------
# Window 3 - Card scanner
# ---------------------------------------------------------------------------


class CardTapWindow:
    """A small, separate window just for NFC card tap lookups - kept apart
    from the camera scanner so the two features (the always-on camera check
    vs. an optional manual card lookup) are visually and spatially distinct.
    Unlike the camera scanner, a card tap is a deliberate, guard-initiated
    lookup, so showing the full result card (photo, name, ID, course) here
    is the whole point - it's what lets the guard visually cross-check the
    tapped card against the person standing in front of them.

    The NFC reader is a cheap HID-keyboard-emulation module (not a PC/SC
    smart card reader): tapping a card makes it "type" the card's ID
    followed by Enter into whatever has OS keyboard focus. So this window
    keeps a hidden, off-screen Entry widget focused at all times to catch
    that input - Tkinter must run on the main thread, so other threads push
    updates through a thread-safe queue instead of touching widgets
    directly. A manual ID-entry fallback covers the case where the reader
    itself fails - the guard types the student/employee ID instead.
    """

    FAILURE_HEADLINES = {
        "not_registered": "✗ Not registered",
        "deactivated": "✗ Deactivated",
        "read_error": "✗ Read error",
    }

    def __init__(self, parent, gate_location, direction, on_tap, on_manual_submit, on_close=None):
        self.direction = direction
        self.on_tap = on_tap
        self.on_manual_submit = on_manual_submit
        self._on_close = on_close
        self._closed = False
        self._reset_job = None

        self.window = ctk.CTkToplevel(parent)
        self.window.title(f"EVSU SecureTap - Card scanner - {gate_location}")
        self.window.configure(fg_color=BG)
        self.window.geometry("420x640")
        self.window.minsize(380, 560)
        self.window.protocol("WM_DELETE_WINDOW", self._handle_close)
        self.window.bind("<Escape>", lambda _e: self._handle_close())
        _apply_icon(self.window)

        self.window.grid_rowconfigure(1, weight=1)
        self.window.grid_columnconfigure(0, weight=1)

        self.header = HeaderBar(self.window, "Card scanner", gate_location, badge=direction.upper())
        self.header.grid(row=0, column=0, sticky="ew")

        self.body = ctk.CTkFrame(self.window, fg_color="transparent")
        self.body.grid(row=1, column=0, sticky="nsew")
        self._build_body()
        self._build_status_bar()

        self._card_input_var = tk.StringVar()
        self._card_input = tk.Entry(self.window, textvariable=self._card_input_var)
        self._card_input.place(x=-500, y=-500)
        self._card_input.bind("<Return>", self._handle_card_input)

        self._queue = queue.Queue()
        self.window.after(100, self._process_queue)
        self.window.after(FOCUS_CHECK_MS, self._keep_focus)

    # ---- layout ---------------------------------------------------------

    def _build_body(self):
        self.body.grid_rowconfigure(0, weight=1)
        self.body.grid_columnconfigure(0, weight=1)

        self.state_area = ctk.CTkFrame(self.body, fg_color="transparent")
        self.state_area.grid(row=0, column=0, sticky="nsew")
        self.state_area.grid_rowconfigure(0, weight=1)
        self.state_area.grid_columnconfigure(0, weight=1)

        self.waiting_frame = ctk.CTkFrame(
            self.state_area, fg_color=CARD_BG, corner_radius=16, border_width=1, border_color=BORDER
        )
        waiting_inner = ctk.CTkFrame(self.waiting_frame, fg_color="transparent")
        waiting_inner.pack(padx=44, pady=44)
        ctk.CTkLabel(waiting_inner, text="(( • ))", font=(FONT, 30, "bold"), text_color=ACCENT).pack()
        ctk.CTkLabel(waiting_inner, text="Tap a card", font=(FONT, 17, "bold"), text_color=TEXT_PRIMARY).pack(
            pady=(16, 2)
        )
        ctk.CTkLabel(
            waiting_inner, text="Hold ID near the reader", font=(FONT, 12), text_color=TEXT_SECONDARY
        ).pack()

        self.result_group = ctk.CTkFrame(self.state_area, fg_color="transparent")
        self.status_label = ctk.CTkLabel(self.result_group, text="", font=(FONT, 15, "bold"))
        self.status_label.pack(pady=(0, 14))
        self.result_card = ResultCard(self.result_group, avatar_size=140)
        self.reason_label = ctk.CTkLabel(
            self.result_group, text="", font=(FONT, 12), text_color=TEXT_SECONDARY,
            wraplength=280, justify="center",
        )

        self._manual_toggle = ctk.CTkButton(
            self.body, text="Enter ID manually", fg_color="transparent", text_color=ACCENT,
            hover_color=BG, font=(FONT, 11), command=self._toggle_manual_entry, height=24,
        )
        self._manual_toggle.grid(row=1, column=0, pady=(6, 4))

        self.manual_frame = ctk.CTkFrame(self.body, fg_color="transparent")
        self._manual_var = tk.StringVar()
        self._manual_entry = ctk.CTkEntry(
            self.manual_frame, textvariable=self._manual_var, placeholder_text="Student/Employee ID", width=190
        )
        self._manual_entry.pack(side="left", padx=(0, 8))
        self._manual_entry.bind("<Return>", lambda _e: self._submit_manual())
        ctk.CTkButton(
            self.manual_frame, text="Submit", command=self._submit_manual,
            fg_color=MAROON, hover_color=MAROON_DARK, width=70,
        ).pack(side="left")

        self._show_waiting()

    def _build_status_bar(self):
        bar = ctk.CTkFrame(self.window, fg_color="transparent")
        bar.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 12))
        self.reader_label = ctk.CTkLabel(bar, text="Reader ready", font=(FONT, 10), text_color=SUCCESS)
        self.reader_label.pack(side="left")
        self.backend_label = ctk.CTkLabel(bar, text="Backend ✓", font=(FONT, 10), text_color=SUCCESS)
        self.backend_label.pack(side="left", padx=(14, 0))
        self.queue_label = ctk.CTkLabel(bar, text="queue 0", font=(FONT, 10), text_color=TEXT_MUTED)
        self.queue_label.pack(side="right")

    def _show_waiting(self):
        self.result_group.grid_forget()
        self.waiting_frame.grid(row=0, column=0)

    def _show_result(self):
        self.waiting_frame.grid_forget()
        self.result_group.grid(row=0, column=0)

    def _toggle_manual_entry(self):
        if self.manual_frame.winfo_ismapped():
            self.manual_frame.grid_forget()
        else:
            self.manual_frame.grid(row=2, column=0, pady=(0, 10))

    def _submit_manual(self):
        value = self._manual_var.get().strip()
        self._manual_var.set("")
        if value and self.on_manual_submit:
            self.on_manual_submit(value)

    # ---- public API -------------------------------------------------------

    def show_match(self, result):
        self._queue.put(("match", result))

    def show_failure(self, reason, reason_code=None):
        self._queue.put(("failure", (reason, reason_code)))

    def show_status(self, message):
        self._queue.put(("status", message))

    def set_queue_count(self, count):
        self._queue.put(("queue_count", count))

    def set_backend_ok(self, ok):
        self._queue.put(("backend_ok", ok))

    # ---- internals ----------------------------------------------------

    def _handle_card_input(self, _event):
        nfc_id = self._card_input_var.get().strip()
        self._card_input_var.set("")
        if not nfc_id:
            self._queue.put(("failure", ("Card read failed - try again.", "read_error")))
            return
        if self.on_tap:
            self.on_tap(nfc_id)

    def _keep_focus(self):
        if self._closed:
            return
        # Don't steal focus back from the manual-entry box while it's open -
        # otherwise every keystroke the guard types there gets yanked away to
        # this hidden field within FOCUS_CHECK_MS, and nothing visible ever
        # gets typed. The reader is HID-keyboard-emulation, so it needs this
        # field focused to catch a tap, but that's only relevant when the
        # guard isn't already deliberately using the manual fallback.
        if self.manual_frame.winfo_ismapped():
            self.window.after(FOCUS_CHECK_MS, self._keep_focus)
            return
        if self.window.focus_get() is not self._card_input:
            self._card_input.focus_force()
        self.window.after(FOCUS_CHECK_MS, self._keep_focus)

    def _process_queue(self):
        if self._closed:
            return
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "match":
                    self._render_match(payload)
                elif kind == "failure":
                    reason, reason_code = payload
                    self._render_failure(reason, reason_code)
                elif kind == "status":
                    self._render_status(payload)
                elif kind == "queue_count":
                    self.queue_label.configure(text=f"queue {payload}")
                elif kind == "backend_ok":
                    if payload:
                        self.backend_label.configure(text="Backend ✓", text_color=SUCCESS)
                    else:
                        self.backend_label.configure(text="Backend ✗", text_color=DANGER)
        except queue.Empty:
            pass
        self.window.after(100, self._process_queue)

    def _render_match(self, result):
        self.status_label.configure(text="✓ Access granted", text_color=SUCCESS)
        self.reason_label.pack_forget()
        self.result_card.update_result(
            photo_bytes=result.get("photo_bytes"),
            name=result.get("name"),
            role=result.get("role"),
            course=result.get("department"),
            student_id=result.get("student_id"),
            card_id=result.get("card_id"),
            direction=self.direction,
            timestamp=datetime.now().strftime("%I:%M:%S %p"),
        )
        self.result_card.pack()
        self._show_result()
        self._schedule_reset()

    def _render_failure(self, reason, reason_code):
        headline = self.FAILURE_HEADLINES.get(reason_code, "✗ Access denied")
        self.status_label.configure(text=headline, text_color=DANGER)
        self.result_card.pack_forget()
        self.reason_label.configure(text=reason or "")
        self.reason_label.pack()
        self._show_result()
        self._schedule_reset()

    def _render_status(self, _message):
        self._show_waiting()

    def _schedule_reset(self):
        if self._reset_job:
            self.window.after_cancel(self._reset_job)
        self._reset_job = self.window.after(RESET_DELAY_MS, self._reset)

    def _reset(self):
        if self._closed:
            return
        self._show_waiting()

    def _handle_close(self):
        self._closed = True
        if self._on_close:
            self._on_close()
        self.window.destroy()
