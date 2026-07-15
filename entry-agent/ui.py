import io
import queue
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime

from PIL import Image, ImageOps, ImageTk

MAROON = "#7B1113"
BG = "#f4f5f7"
CARD_BG = "#ffffff"
CARD_BORDER = "#e5e7eb"
TEXT_DARK = "#1f2937"
TEXT_MUTED = "#6b7280"
SUCCESS_BG = "#16a34a"
SUCCESS_TEXT = "#f0fdf4"
FAILURE_BG = "#dc2626"
FAILURE_TEXT = "#fef2f2"
IDLE_BG = "#374151"
IDLE_TEXT = "#e5e7eb"
PLACEHOLDER_AVATAR = "#d1d5db"

FOCUS_CHECK_MS = 300
RESET_DELAY_MS = 6000


def _avatar_image(raw_bytes, size):
    """Crops/resizes raw_bytes to a plain size x size square photo, or a
    solid placeholder square if raw_bytes is falsy/unreadable. Deliberately
    square, not circular/rounded - the person's actual photo."""
    if raw_bytes:
        try:
            source = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
            return ImageOps.fit(source, (size, size), Image.LANCZOS)
        except Exception:
            pass
    return Image.new("RGB", (size, size), PLACEHOLDER_AVATAR)


def _build_header(parent, title, subtitle, row=0):
    header = tk.Frame(parent, bg=MAROON)
    header.grid(row=row, column=0, sticky="ew")
    tk.Label(header, text=title, font=("Segoe UI", 14, "bold"), bg=MAROON, fg="white").pack(pady=(8, 0))
    tk.Label(header, text=subtitle, font=("Segoe UI", 9), bg=MAROON, fg="#f3c6c7").pack(pady=(1, 8))
    return header


class _StatusBanner:
    """A colored, capsule-shaped status bar: idle (gray) / granted (green) /
    denied (red), with an icon, a headline, and an optional reason line.
    Shared by both windows so they read as one consistent system.

    Everything (the rounded background shape and the text) is drawn
    directly on a Canvas rather than embedding a Frame/Label inside it -
    an earlier version tried the embedded-widget approach and the text
    silently failed to reliably show up, since it depended on Tkinter
    propagating the embedded Frame's size back to the Canvas via
    <Configure> events. Drawing text straight onto the canvas with
    create_text has no such indirection - what gets measured is exactly
    what gets drawn.
    """

    PAD_X = 16
    PAD_Y = 8
    LINE_GAP = 3

    def __init__(self, parent, row, stretch=True):
        self._stretch = stretch
        self._bg = IDLE_BG
        self._fg = IDLE_TEXT
        self._icon = "●"
        self._text = "SCANNING..."
        self._reason = ""
        self._headline_font = tkfont.Font(family="Segoe UI", size=10, weight="bold")
        self._reason_font = tkfont.Font(family="Segoe UI", size=8)

        self.canvas = tk.Canvas(parent, highlightthickness=0, bg=parent["bg"])
        self.canvas.grid(row=row, column=0, sticky=("ew" if stretch else ""), padx=16, pady=(4, 4))
        if stretch:
            self.canvas.bind("<Configure>", lambda _event: self._redraw())
        self._redraw()

    def set(self, bg, fg, icon, text, reason=""):
        self._bg, self._fg, self._icon, self._text, self._reason = bg, fg, icon, text, reason
        self._redraw()

    def idle(self, message="SCANNING..."):
        self.set(IDLE_BG, IDLE_TEXT, "●", message.upper())

    def granted(self, headline="ACCESS GRANTED"):
        self.set(SUCCESS_BG, SUCCESS_TEXT, "✓", headline)

    def denied(self, reason=""):
        self.set(FAILURE_BG, FAILURE_TEXT, "✗", "ACCESS DENIED", reason=reason)

    def _redraw(self):
        headline = f"{self._icon}  {self._text}"
        headline_w = self._headline_font.measure(headline)
        headline_h = self._headline_font.metrics("linespace")

        content_w = headline_w
        content_h = headline_h
        reason_h = 0
        if self._reason:
            reason_h = self._reason_font.metrics("linespace")
            content_w = max(content_w, self._reason_font.measure(self._reason))
            content_h += self.LINE_GAP + reason_h

        box_w = content_w + self.PAD_X * 2
        box_h = content_h + self.PAD_Y * 2

        if self._stretch:
            available = self.canvas.winfo_width()
            if available > 1:
                box_w = max(box_w, available)
            self.canvas.configure(height=box_h)
        else:
            self.canvas.configure(width=box_w, height=box_h)

        self.canvas.delete("all")
        self._draw_round_rect(0, 0, box_w, box_h, box_h / 2, fill=self._bg)

        center_x = box_w / 2
        y = self.PAD_Y + headline_h / 2
        self.canvas.create_text(center_x, y, text=headline, font=self._headline_font, fill=self._fg)
        if self._reason:
            y += headline_h / 2 + self.LINE_GAP + reason_h / 2
            self.canvas.create_text(
                center_x, y, text=self._reason, font=self._reason_font, fill=self._fg,
                width=box_w - self.PAD_X * 2,
            )

    def _draw_round_rect(self, x1, y1, x2, y2, radius, **kwargs):
        radius = max(0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
        points = [
            x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
            x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
            x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
        ]
        self.canvas.create_polygon(points, smooth=True, outline="", **kwargs)


class FeedbackWindow:
    """The main gate display, opened on demand from the MenuWindow: a live
    camera preview (with a box on every detected face) and, below it, a
    colored status banner. That banner only ever shows GRANTED/DENIED/
    SCANNING - no name, photo, or other identifying details - because the
    Live Monitoring page on the dashboard is now the place for tracking who
    specifically was scanned; this window's job is just to be a clear,
    glanceable gate indicator plus the raw camera feed for a guard standing
    next to it.

    NFC card taps are handled by a separate CardTapWindow (see below), kept
    apart so the always-on camera scan and the optional card lookup read as
    two distinct tools rather than one crowded window - both can be open at
    once, launched independently from the menu.
    """

    VIDEO_REFRESH_MS = 80  # ~12 fps - smooth enough for a security preview
    MIN_VIDEO_SIZE = (560, 440)

    def __init__(self, parent, gate_location, get_preview_frame=None, on_close=None):
        self._get_preview_frame = get_preview_frame
        self.gate_location = gate_location
        self._on_close = on_close
        self._closed = False

        self.root = tk.Toplevel(parent)
        self.root.title(f"EVSU SecureTap - {gate_location}")
        self.root.configure(bg=BG)
        self.root.attributes("-topmost", True)
        self.root.resizable(True, True)
        self.root.minsize(600, 700)
        self.root.geometry("720x860")
        self.root.grid_rowconfigure(1, weight=1)
        self.root.grid_columnconfigure(0, weight=1)
        self.root.protocol("WM_DELETE_WINDOW", self._handle_close)

        self._video_image = None
        self._reset_job = None

        _build_header(self.root, "EVSU SecureTap", f"Campus Entry Monitoring - {gate_location}", row=0)
        self._build_video()
        self.banner = _StatusBanner(self.root, row=2, stretch=False)
        self._build_footer()

        self._queue = queue.Queue()
        self.root.after(100, self._process_queue)
        if self._get_preview_frame:
            self.root.after(self.VIDEO_REFRESH_MS, self._update_video)

    # ---- layout ---------------------------------------------------------

    def _build_video(self):
        self.video_frame = tk.Frame(self.root, bg=BG)
        self.video_frame.grid(row=1, column=0, sticky="nsew", padx=16, pady=(16, 4))
        self.video_frame.grid_rowconfigure(0, weight=1)
        self.video_frame.grid_columnconfigure(0, weight=1)

        self.video_label = tk.Label(
            self.video_frame, bg="#111827", fg="#9ca3af", text="Starting camera...",
            font=("Segoe UI", 10),
        )
        self.video_label.grid(row=0, column=0, sticky="nsew")

    def _build_footer(self):
        self.footer_label = tk.Label(
            self.root, text="Waiting for the first scan...", font=("Segoe UI", 8), bg=BG, fg=TEXT_MUTED
        )
        self.footer_label.grid(row=3, column=0, pady=(0, 12))

    # ---- public API -------------------------------------------------------

    def show_matches(self, count):
        """count: how many people were recognized in this scan cycle - just
        a number, no names or photos surface here by design."""
        self._queue.put(("matches", count))

    def show_failure(self, reason):
        self._queue.put(("failure", reason))

    def show_status(self, message):
        self._queue.put(("status", message))

    # ---- internals ----------------------------------------------------

    def _update_video(self):
        if self._closed:
            return
        frame = self._get_preview_frame()
        if frame is not None:
            rgb = frame[:, :, ::-1]  # BGR (OpenCV) -> RGB, no cv2 import needed here
            image = Image.fromarray(rgb)
            image = image.resize(self._scaled_size(image.size), Image.LANCZOS)
            self._video_image = ImageTk.PhotoImage(image)
            self.video_label.configure(image=self._video_image, text="", width=0, height=0)
        self.root.after(self.VIDEO_REFRESH_MS, self._update_video)

    def _video_target_size(self):
        width = self.video_frame.winfo_width() or self.MIN_VIDEO_SIZE[0]
        height = self.video_frame.winfo_height() or self.MIN_VIDEO_SIZE[1]
        return (max(width - 4, self.MIN_VIDEO_SIZE[0]), max(height - 4, self.MIN_VIDEO_SIZE[1]))

    def _scaled_size(self, source_size):
        """Fits source_size into the current video area preserving aspect
        ratio - unlike Image.thumbnail(), this scales up too, so the preview
        actually grows to fill the window when maximized/full screen instead
        of staying pinned at the webcam's native resolution."""
        source_width, source_height = source_size
        target_width, target_height = self._video_target_size()
        if source_width <= 0 or source_height <= 0:
            return target_width, target_height
        scale = min(target_width / source_width, target_height / source_height)
        return max(1, int(source_width * scale)), max(1, int(source_height * scale))

    def _process_queue(self):
        if self._closed:
            return
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "matches":
                    self._render_matches(payload)
                elif kind == "failure":
                    self._render_failure(payload)
                elif kind == "status":
                    self._render_status(payload)
        except queue.Empty:
            pass
        self.root.after(100, self._process_queue)

    def _render_matches(self, count):
        if not count:
            self._render_status("Scanning...")
            return
        headline = "ACCESS GRANTED" if count == 1 else f"ACCESS GRANTED ({count})"
        self.banner.granted(headline)
        self._touch_footer()
        self._schedule_reset()

    def _render_failure(self, reason):
        self.banner.denied(reason or "")
        self._touch_footer()
        self._schedule_reset()

    def _render_status(self, message):
        self.banner.idle(message)

    def _touch_footer(self):
        self.footer_label.configure(text=f"Last scan: {datetime.now().strftime('%I:%M:%S %p')}")

    def _schedule_reset(self):
        if self._reset_job:
            self.root.after_cancel(self._reset_job)
        self._reset_job = self.root.after(RESET_DELAY_MS, self._reset)

    def _reset(self):
        if self._closed:
            return
        self.banner.idle()

    def _handle_close(self):
        self._closed = True
        if self._on_close:
            self._on_close()
        self.root.destroy()


class CardTapWindow:
    """A small, separate always-on-top window just for NFC card tap lookups -
    kept apart from the main camera-scan display so the two features (the
    always-on camera check vs. an optional manual card lookup) are visually
    and spatially distinct rather than crowding one window. Unlike the
    camera scanner, a card tap is a deliberate, guard-initiated lookup, so
    showing the full profile (photo, name, role, department) here is the
    whole point - it's what lets the guard visually cross-check the tapped
    card against the person standing in front of them.

    The NFC reader is a cheap HID-keyboard-emulation module (not a PC/SC
    smart card reader): tapping a card makes it "type" the card's ID
    followed by Enter into whatever has OS keyboard focus. So this window
    keeps a hidden, off-screen Entry widget focused at all times to catch
    that input - Tkinter must run on the main thread, so other threads push
    updates through a thread-safe queue instead of touching widgets directly.
    """

    MIN_AVATAR_SIZE = 160
    MAX_AVATAR_SIZE = 320

    def __init__(self, parent, gate_location, on_tap, on_close=None):
        self.on_tap = on_tap
        self._on_close = on_close
        self._closed = False
        self._avatar_image = None
        self._last_profile = None
        self._resize_job = None

        self.window = tk.Toplevel(parent)
        self.window.title("EVSU SecureTap - Card Lookup")
        self.window.configure(bg=BG)
        self.window.attributes("-topmost", True)
        self.window.resizable(True, True)
        self.window.minsize(360, 480)
        self.window.geometry("400x560")
        self.window.grid_rowconfigure(2, weight=1)
        self.window.grid_columnconfigure(0, weight=1)
        self.window.protocol("WM_DELETE_WINDOW", self._handle_close)

        _build_header(self.window, "Card Lookup", gate_location, row=0)
        self.banner = _StatusBanner(self.window, row=1, stretch=False)

        self.card_area = tk.Frame(self.window, bg=BG)
        self.card_area.grid(row=2, column=0, sticky="nsew")
        self.card_area.grid_rowconfigure(0, weight=1)
        self.card_area.grid_columnconfigure(0, weight=1)
        self.card_area.bind("<Configure>", self._on_card_area_resize)

        # A plain bordered frame, not the canvas-drawn rounded panel used for
        # the status banner - that trick doesn't reliably resize around a
        # dynamically-sized photo, so this sticks to a proven, simple layout.
        self.card_frame = tk.Frame(
            self.card_area, bg=CARD_BG, highlightthickness=1, highlightbackground=CARD_BORDER
        )
        # Not gridded initially - _render_match()/_reset() show or hide it.

        inner = tk.Frame(self.card_frame, bg=CARD_BG)
        inner.pack(padx=32, pady=28)

        self.avatar_label = tk.Label(inner, bg=CARD_BG)
        self.avatar_label.pack(pady=(0, 16))
        self.name_label = tk.Label(inner, text="", font=("Segoe UI", 20, "bold"), bg=CARD_BG, fg=TEXT_DARK)
        self.name_label.pack()
        self.role_label = tk.Label(inner, text="", font=("Segoe UI", 12, "bold"), bg=CARD_BG, fg=MAROON)
        self.role_label.pack(pady=(6, 0))
        self.id_label = tk.Label(inner, text="", font=("Segoe UI", 10), bg=CARD_BG, fg=TEXT_MUTED)
        self.id_label.pack(pady=(8, 0))
        self.department_label = tk.Label(
            inner, text="", font=("Segoe UI", 10), bg=CARD_BG, fg=TEXT_MUTED,
            wraplength=280, justify="center",
        )
        self.department_label.pack(pady=(2, 0))

        self._card_input_var = tk.StringVar()
        self._card_input = tk.Entry(self.window, textvariable=self._card_input_var)
        self._card_input.place(x=-500, y=-500)
        self._card_input.bind("<Return>", self._handle_card_input)

        self._queue = queue.Queue()
        self._reset_job = None
        self.window.after(100, self._process_queue)
        self.window.after(FOCUS_CHECK_MS, self._keep_focus)
        self.banner.idle("Tap a card...")

    # ---- public API -------------------------------------------------------

    def show_match(self, profile):
        self._queue.put(("match", profile))

    def show_failure(self, reason):
        self._queue.put(("failure", reason))

    def show_status(self, message):
        self._queue.put(("status", message))

    # ---- internals ----------------------------------------------------

    def _handle_card_input(self, _event):
        nfc_id = self._card_input_var.get().strip()
        self._card_input_var.set("")
        if nfc_id and self.on_tap:
            self.on_tap(nfc_id)

    def _keep_focus(self):
        if self._closed:
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
                    self._render_failure(payload)
                elif kind == "status":
                    self._render_status(payload)
        except queue.Empty:
            pass
        self.window.after(100, self._process_queue)

    def _on_card_area_resize(self, _event):
        if self._closed:
            return
        if self._resize_job:
            self.window.after_cancel(self._resize_job)
        self._resize_job = self.window.after(120, self._refresh_avatar_size)

    def _refresh_avatar_size(self):
        if not self._closed and self._last_profile is not None:
            self._render_avatar(self._last_profile)

    # Reserves room for the name/role/ID/course text block (plus outer card
    # padding) below the avatar, so a large photo can never push those rows
    # past the bottom of a fixed-size window and clip them out of view.
    TEXT_BLOCK_RESERVE = 200

    def _current_avatar_size(self):
        available_height = self.card_area.winfo_height() - self.TEXT_BLOCK_RESERVE
        available_width = self.card_area.winfo_width() - 80
        available = min(available_width, available_height)
        if available <= 0:
            return self.MIN_AVATAR_SIZE
        return max(self.MIN_AVATAR_SIZE, min(self.MAX_AVATAR_SIZE, available))

    def _render_avatar(self, profile):
        avatar = _avatar_image(profile.get("photo_bytes"), self._current_avatar_size())
        self._avatar_image = ImageTk.PhotoImage(avatar)
        self.avatar_label.configure(image=self._avatar_image)

    def _render_match(self, profile):
        self.banner.granted()
        self._last_profile = profile
        self._render_avatar(profile)

        self.name_label.configure(text=profile.get("name") or "Unknown")
        self.role_label.configure(text=(profile.get("role") or "").capitalize())
        self.id_label.configure(text=f"ID: {profile.get('student_id') or '—'}")
        self.department_label.configure(text=f"Course: {profile.get('department') or '—'}")

        self.card_frame.grid(row=0, column=0)
        self._schedule_reset()

    def _render_failure(self, reason):
        self.banner.denied(reason or "")
        self.card_frame.grid_forget()
        self._last_profile = None
        self._schedule_reset()

    def _render_status(self, message):
        self.banner.idle(message)
        self.card_frame.grid_forget()
        self._last_profile = None

    def _schedule_reset(self):
        if self._reset_job:
            self.window.after_cancel(self._reset_job)
        self._reset_job = self.window.after(RESET_DELAY_MS, self._reset)

    def _reset(self):
        if self._closed:
            return
        self.banner.idle("Tap a card...")
        self.card_frame.grid_forget()
        self._last_profile = None

    def _handle_close(self):
        self._closed = True
        if self._on_close:
            self._on_close()
        self.window.destroy()


class MenuWindow:
    """The first thing that opens when entry-agent starts: a small launcher
    that lets the guard choose which scanner to open. Both the camera
    scanner and the card scanner can be open at the same time - this window
    just starts them, it doesn't own or replace them. Closing this window
    exits the whole app; closing a scanner window just returns control here
    (the scanner itself notifies this window via its on_close callback).
    """

    def __init__(self, gate_location, on_open_camera, on_open_card, on_exit):
        self.root = tk.Tk()
        self.root.title("EVSU SecureTap")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", on_exit)

        _build_header(self.root, "EVSU SecureTap", f"{gate_location} - choose a scanner to open", row=0)

        button_area = tk.Frame(self.root, bg=BG)
        button_area.grid(row=1, column=0, padx=36, pady=28)

        self._make_button(button_area, "Open Camera Scanner", on_open_camera).pack(fill="x", pady=(0, 14))
        self._make_button(button_area, "Open Card Scanner", on_open_card).pack(fill="x")

    @staticmethod
    def _make_button(parent, text, command):
        return tk.Button(
            parent, text=text, command=command, font=("Segoe UI", 12, "bold"),
            bg=MAROON, fg="white", activebackground="#5c0d0e", activeforeground="white",
            relief="flat", padx=24, pady=16, cursor="hand2",
        )

    def run_forever(self):
        self.root.mainloop()
