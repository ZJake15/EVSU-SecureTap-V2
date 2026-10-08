"""The student display - a second, student-facing screen for the gate.

The gate monitor is the guard's screen: stats, the live log, confidence
scores, alarm banners. This one faces the people walking through and tells
each of them, in large plain words, what is happening and what to do -
"Welcome, Maria", "Please uncover your face", "Tap your ID card". It shows
the same camera feed, mirrored like a mirror so people can find themselves
in it, with first names only (it's on public view).

Opened and closed from the gate monitor's status bar ("Open student
display"). It owns no scanning of its own: the monitor hands it every
/api/identify result and card tap it receives, so the two screens can never
disagree. On a second monitor (a TV at the gate) it opens fullscreen there;
otherwise it opens as a window that can be dragged to one - F11 or a
double-click toggles fullscreen.

Laid out from docs/Redesign UI/Student Display.dc.html, a 1920x1080 design,
drawn on one canvas and scaled to whatever size the window has (letterboxed,
so the proportions never change).
"""

import time
import tkinter as tk
import tkinter.font as tkfont
import traceback
from datetime import datetime

from PIL import Image, ImageTk

from ui import (
    BRASS, CAUTION, COND_HEAVY, DANGER, FONT, FONT_MONO_MEDIUM, ICON_FONT, ICON_FONT_BOLD, ICON_PATH, INK,
    INK_400, INK_600, LINE, MAROON, PROMPT, SEMI_HEAVY, SURFACE, VERIFIED, WIDE_BLACK,
    _apply_icon, _ellipsize, _icon, _rounded_rect_points, _screen_work_area,
)

DESIGN_W, DESIGN_H = 1920, 1080
HEADER_H, RULE_H, BAR_H = 96, 3, 132
VIDEO_TOP, VIDEO_BOTTOM = HEADER_H + RULE_H, DESIGN_H - BAR_H
PAD = 42

REFRESH_MS = 100  # ~10 fps - a second full-screen redraw beside the monitor's own
# Same rule as the gate monitor's feed: fill the area edge to edge unless
# that would trim more than this much of the picture.
MAX_FEED_CROP = 0.35
# How long a message stays up after the situation that caused it has gone,
# so the bar doesn't flicker back to "Look at the camera" between frames.
MESSAGE_HOLD_SECONDS = 4.0
# How long a card tap's result owns the message bar.
CARD_MESSAGE_SECONDS = 6.0

# What a face's box says, by outcome: (color, icon, label). "entry" takes the
# person's first name; "checking" takes the backend's hint when it has one.
FACE_STYLES = {
    "entry": (VERIFIED, "check-circle", "Welcome, {name}"),
    "exit": (VERIFIED, "check-circle", "Goodbye, {name}"),
    "unknown": (CAUTION, "user-circle-dashed", "Please see the guard"),
    "spoof": (DANGER, "warning-octagon", "Show your real face"),
    "uncover": (PROMPT, "hand-palm", "Please uncover your face"),
    "tap": (PROMPT, "identification-card", "Tap your ID card"),
    "checking": (INK_600, "circle-notch", "Checking…"),
}

# The message bar: (background, text color, icon, title, subtitle).
MESSAGES = {
    "ok": (SURFACE, INK, "scan-smiley", "Look at the camera as you walk in",
           "Keep your face uncovered. Tap your ID card if asked."),
    "welcome": (VERIFIED, SURFACE, "check-circle", "Entry recorded", "Have a good day."),
    "goodbye": (VERIFIED, SURFACE, "check-circle", "Exit recorded", "Take care."),
    "guard": (CAUTION, SURFACE, "hand", "Please wait — the guard will assist you", "We could not recognize you."),
    "spoof": (DANGER, SURFACE, "warning-octagon", "Photos and screens are not accepted",
              "Please step forward and look at the camera yourself."),
    "uncover": (PROMPT, SURFACE, "hand-palm", "Please uncover your face",
                "Remove your mask, cap or hand so the camera can see you."),
    "tap": (PROMPT, SURFACE, "identification-card", "Tap your ID card on the reader", "This confirms who you are."),
    "card": (DANGER, SURFACE, "identification-card", "This card is not registered", "Please see the guard."),
    "card_inactive": (DANGER, SURFACE, "identification-card", "This card is no longer active", "Please see the guard."),
    "card_retry": (PROMPT, SURFACE, "identification-card", "Please tap your ID card again",
                   "The card could not be read."),
    "nocam": (PROMPT, SURFACE, "identification-card", "Tap your ID card on the reader",
              "Hold it flat against the reader until it beeps."),
}

# When several faces are in frame, the message bar speaks to the one that
# most needs something done - highest first.
MESSAGE_PRIORITY = ("spoof", "guard", "tap", "uncover", "welcome", "goodbye")


def first_name(full_name):
    """First name only, for a screen anyone at the gate can read. Handles
    both "Maria Santos" and "Santos, Maria"."""
    name = (full_name or "").strip()
    if "," in name:
        name = name.split(",", 1)[1].strip() or name.split(",", 1)[0]
    return name.split()[0] if name.split() else "there"


def face_kind(item):
    """Which FACE_STYLES entry one /api/identify result shows as."""
    if item.get("spoof_suspected"):
        # Only once the backend has confirmed it - a single low-liveness
        # frame is still "checking" to the person in front of the camera.
        return "checking" if item.get("retry") else "spoof"
    if item.get("occlusion_suspected"):
        return "uncover"
    if item.get("retry"):
        return "checking"
    if item.get("tiebreak"):
        return "tap"
    if item.get("matched"):
        return "exit" if item.get("direction") == "exit" else "entry"
    return "unknown"


FACE_MESSAGE = {"spoof": "spoof", "unknown": "guard", "tap": "tap", "uncover": "uncover",
                "entry": "welcome", "exit": "goodbye"}


def _secondary_monitor():
    """(left, top, right, bottom) of a monitor other than the main one, in
    real pixels, or None with a single screen. Windows-only - elsewhere it
    just reports no second screen."""
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
        return next((m for m in monitors if (m[0], m[1]) != (0, 0)), None)
    except Exception:
        return None


class StudentDisplayWindow:
    def __init__(self, master, gate_location, direction, get_preview_frame, is_camera_down,
                 on_card_key, on_close):
        self._get_preview_frame = get_preview_frame
        self._is_camera_down = is_camera_down
        self._on_close = on_close
        self.gate_location = gate_location or "Gate"
        self.direction = (direction or "entry").lower()
        self._closed = False

        self._recognitions = []
        self._image_size = (1, 1)
        self._message = "ok"
        self._message_at = 0.0
        self._card_message = None  # (key, monotonic time) while a tap's result shows
        self._fonts = {}
        self._seal_cache = (None, None)  # (size, PhotoImage)
        self._frame_image = None  # keeps the current PhotoImage alive

        self.window = tk.Toplevel(master)
        self.window.title(f"EVSU SecureTap - Student display - {self.gate_location}")
        self.window.configure(bg=INK)
        _apply_icon(self.window)
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        self.canvas = tk.Canvas(self.window, bg=INK, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        # The card reader types into whichever window has focus - if a guard
        # clicks on this one, taps still have to reach the gate monitor.
        self.window.bind("<Key>", on_card_key)
        self.window.bind("<F11>", lambda _e: self.toggle_fullscreen())
        self.window.bind("<Double-Button-1>", lambda _e: self.toggle_fullscreen())
        self.window.bind("<Escape>", lambda _e: self.set_fullscreen(False))

        self._place()
        self.window.after(REFRESH_MS, self._tick)

    # ---- window placement -------------------------------------------------

    def _place(self):
        monitor = _secondary_monitor()
        if monitor:
            left, top, right, bottom = monitor
            self.window.geometry(f"{right - left}x{bottom - top}+{left}+{top}")
            # Fullscreen goes to whichever monitor the window is on, so it
            # has to be placed there first.
            self.window.after(250, lambda: self.set_fullscreen(True))
        else:
            work_w, _work_h = _screen_work_area(self.window)
            width = round(work_w * 0.6)
            self.window.geometry(f"{width}x{round(width * 9 / 16)}+60+60")

    def set_fullscreen(self, on):
        if not self._closed:
            self.window.attributes("-fullscreen", bool(on))

    def toggle_fullscreen(self):
        if not self._closed:
            self.set_fullscreen(not self.window.attributes("-fullscreen"))

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self.window.destroy()
        except tk.TclError:
            pass
        if self._on_close:
            self._on_close()

    def is_closed(self):
        return self._closed or not self.window.winfo_exists()

    def owns(self, widget):
        """Whether `widget` lives in this window (for the gate monitor's
        focus keeper, which must not pull focus away from it)."""
        return widget is not None and str(widget).startswith(str(self.window))

    def lift(self):
        if not self._closed:
            self.window.deiconify()
            self.window.lift()

    # ---- fed by the gate monitor ----------------------------------------

    def update_recognitions(self, recognitions, image_size):
        self._recognitions = recognitions
        self._image_size = image_size
        kinds = {FACE_MESSAGE.get(face_kind(item)) for item in recognitions}
        key = next((k for k in MESSAGE_PRIORITY if k in kinds), "ok")
        now = time.monotonic()
        # A new situation shows straight away; going back to the idle prompt
        # waits a moment, so it doesn't flash up between two frames.
        if key != "ok" or now - self._message_at >= MESSAGE_HOLD_SECONDS:
            if key != "ok":
                self._message_at = now
            self._message = key

    def show_card_result(self, outcome):
        """outcome: "accepted", "queued" (offline, will sync), "rejected"
        (not registered), "deactivated", or "error" (misread or a server
        problem - worth tapping again)."""
        key = {"rejected": "card", "deactivated": "card_inactive", "error": "card_retry"}.get(outcome)
        if key is None:
            key = "goodbye" if self.direction == "exit" else "welcome"
        self._card_message = (key, time.monotonic())

    # ---- drawing --------------------------------------------------------

    def _font(self, family, px):
        px = max(1, round(px * self._k))
        key = (family, px)
        if key not in self._fonts:
            # Negative size = pixels, so the text scales with the window
            # rather than with Windows' display-scaling setting.
            self._fonts[key] = tkfont.Font(root=self.window, family=family, size=-px)
        return self._fonts[key]

    def _x(self, design_x):
        return self._ox + design_x * self._k

    def _y(self, design_y):
        return self._oy + design_y * self._k

    def _text(self, x, y, text, family, px, color, anchor="w"):
        return self.canvas.create_text(x, y, text=text, font=self._font(family, px), fill=color, anchor=anchor)

    def _tick(self):
        if self.is_closed():
            return
        try:
            self._draw()
        except tk.TclError:
            if self.is_closed():
                return  # torn down mid-draw
            traceback.print_exc()
        except Exception:
            # Logged, then the next frame tries again - never a frozen screen.
            traceback.print_exc()
        self.window.after(REFRESH_MS, self._tick)

    def _draw(self):
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        if width < 2 or height < 2:
            return
        self._k = min(width / DESIGN_W, height / DESIGN_H)
        self._ox = (width - DESIGN_W * self._k) / 2
        self._oy = (height - DESIGN_H * self._k) / 2
        self.canvas.delete("all")

        camera_down = self._is_camera_down()
        self._draw_video(camera_down)
        self._draw_header()
        self._draw_message_bar(camera_down)

    def _draw_header(self):
        c = self.canvas
        c.create_rectangle(self._x(0), self._y(0), self._x(DESIGN_W), self._y(HEADER_H), fill=MAROON, outline="")
        c.create_rectangle(self._x(0), self._y(HEADER_H), self._x(DESIGN_W), self._y(VIDEO_TOP), fill=BRASS, outline="")
        mid = self._y(HEADER_H / 2)

        seal = self._seal(round(56 * self._k))
        if seal:
            c.create_image(self._x(PAD), mid, image=seal, anchor="w")

        clock_id = self._text(self._x(DESIGN_W - PAD), mid, datetime.now().strftime("%I:%M:%S %p"),
                              FONT_MONO_MEDIUM, 36, SURFACE, anchor="e")
        text_x = self._x(PAD + 56 + 20)
        title_room = c.bbox(clock_id)[0] - self._k * 26 - text_x
        self._text(text_x, self._y(21), f"EVSU · {self.gate_location.upper()}", WIDE_BLACK, 14, SURFACE, anchor="nw")
        title = "See you next time" if self.direction == "exit" else "Welcome to campus"
        self._text(text_x, self._y(39), _ellipsize(self._font(SEMI_HEAVY, 36), title, title_room),
                   SEMI_HEAVY, 36, SURFACE, anchor="nw")

    def _seal(self, size):
        cached_size, image = self._seal_cache
        if cached_size != size:
            try:
                image = ImageTk.PhotoImage(Image.open(ICON_PATH).convert("RGBA").resize((size, size), Image.LANCZOS))
            except Exception:
                image = None
            self._seal_cache = (size, image)
        return image

    def _draw_video(self, camera_down):
        c = self.canvas
        x0, y0, x1, y1 = self._x(0), self._y(VIDEO_TOP), self._x(DESIGN_W), self._y(VIDEO_BOTTOM)
        frame = None if camera_down else self._get_preview_frame()
        if frame is None:
            c.create_rectangle(x0, y0, x1, y1, fill=INK, outline="")
            if camera_down:
                self._draw_no_camera((x0 + x1) / 2, (y0 + y1) / 2)
            self._draw_live_chip(x1, y0, live=not camera_down)
            return

        c.create_rectangle(x0, y0, x1, y1, fill=INK_600, outline="")
        area_w, area_h = max(1, round(x1 - x0)), max(1, round(y1 - y0))
        # Mirrored, like a mirror - people find themselves in it naturally.
        image = Image.fromarray(frame[:, :, ::-1]).transpose(Image.FLIP_LEFT_RIGHT)
        src_w, src_h = image.size
        cover = max(area_w / src_w, area_h / src_h)
        crop = 1 - min(area_w / (src_w * cover), area_h / (src_h * cover))
        scale = cover if crop <= MAX_FEED_CROP else min(area_w / src_w, area_h / src_h)
        img_w, img_h = max(1, round(src_w * scale)), max(1, round(src_h * scale))
        off_x, off_y = (area_w - img_w) // 2, (area_h - img_h) // 2
        visible = image.resize((img_w, img_h), Image.BILINEAR).crop(
            (max(0, -off_x), max(0, -off_y), min(img_w, area_w - off_x), min(img_h, area_h - off_y))
        )
        self._frame_image = ImageTk.PhotoImage(visible)
        c.create_image(x0 + max(0, off_x), y0 + max(0, off_y), image=self._frame_image, anchor="nw")

        bounds = (x0, y0, x1, y1)
        placement = (x0 + off_x, y0 + off_y, img_w, img_h)
        for item in self._recognitions:
            self._draw_face(item, placement, bounds)
        self._draw_live_chip(x1, y0, live=True)

    def _draw_face(self, item, placement, bounds):
        box = item.get("box")
        src_w, src_h = self._image_size
        if not box or src_w <= 0 or src_h <= 0:
            return
        origin_x, origin_y, img_w, img_h = placement
        feed_x0, feed_y0, feed_x1, feed_y1 = bounds
        # Mirrored to match the picture: the box's right edge becomes its left.
        x0 = origin_x + ((src_w - box["right"]) / src_w) * img_w
        x1 = origin_x + ((src_w - box["left"]) / src_w) * img_w
        y0 = origin_y + (box["top"] / src_h) * img_h
        y1 = origin_y + (box["bottom"] / src_h) * img_h
        x0, y0, x1, y1 = max(x0, feed_x0), max(y0, feed_y0), min(x1, feed_x1 - 1), min(y1, feed_y1 - 1)
        k = self._k
        if x1 - x0 < 8 * k or y1 - y0 < 8 * k:
            return

        kind = face_kind(item)
        color, icon_name, label = FACE_STYLES[kind]
        if kind in ("entry", "exit"):
            label = label.format(name=first_name(item.get("name")))
        elif kind == "checking" and item.get("hint"):
            label = item["hint"]

        self.canvas.create_polygon(
            _rounded_rect_points(x0, y0, x1, y1, 8 * k), outline=color, fill="", width=max(1, round(4 * k))
        )

        # The tab above the box: icon + words, white on the outcome's color.
        text_font = self._font(SEMI_HEAVY, 32)
        glyph = _icon(icon_name)
        icon_w = (32 + 10) * k if glyph else 0
        tab_w = icon_w + text_font.measure(label) + 32 * k
        tab_h = 52 * k
        top = y0 - 6 * k - tab_h
        if top < feed_y0 + 4 * k:
            top = y0 + 6 * k  # no room above - tuck it inside the box
        left = max(feed_x0 + 4 * k, min(x0, feed_x1 - tab_w - 4 * k))
        self.canvas.create_polygon(_rounded_rect_points(left, top, left + tab_w, top + tab_h, 8 * k),
                                   fill=color, outline="")
        cy = top + tab_h / 2
        if glyph:
            self._text(left + 16 * k, cy, glyph, ICON_FONT_BOLD, 32, SURFACE)
        self._text(left + 16 * k + icon_w, cy, label, SEMI_HEAVY, 32, SURFACE)

    def _draw_live_chip(self, right, top, live):
        k = self._k
        word_font = self._font(COND_HEAVY, 18)
        word = "LIVE" if live else "NO SIGNAL"
        width = (14 + 10 + 10 + 14) * k + word_font.measure(word)
        height = 36 * k
        x1, y0 = right - 26 * k, top + 20 * k
        x0 = x1 - width
        self.canvas.create_rectangle(x0, y0, x1, y0 + height, fill=INK, outline="")
        cy = y0 + height / 2
        self.canvas.create_rectangle(x0 + 14 * k, cy - 5 * k, x0 + 24 * k, cy + 5 * k,
                                     fill=BRASS if live else INK_400, outline="")
        self._text(x0 + 34 * k, cy, word, COND_HEAVY, 18, SURFACE)

    def _draw_no_camera(self, cx, cy):
        k = self._k
        glyph = _icon("identification-card", bold=False)
        top = cy - 116 * k
        if glyph:
            self._text(cx, top, glyph, ICON_FONT, 96, BRASS, anchor="n")
        self._text(cx, top + 116 * k, "Please tap your ID card", SEMI_HEAVY, 56, SURFACE, anchor="n")
        self._text(cx, top + 198 * k, "The camera is unavailable right now.", FONT, 28, LINE, anchor="n")

    def _current_message(self, camera_down):
        if self._card_message:
            key, at = self._card_message
            if time.monotonic() - at < CARD_MESSAGE_SECONDS:
                return key
            self._card_message = None
        if camera_down:
            return "nocam"
        return self._message

    def _draw_message_bar(self, camera_down):
        k = self._k
        bg, fg, icon_name, title, sub = MESSAGES[self._current_message(camera_down)]
        self.canvas.create_rectangle(self._x(0), self._y(VIDEO_BOTTOM), self._x(DESIGN_W), self._y(DESIGN_H),
                                     fill=bg, outline="")
        mid = self._y(VIDEO_BOTTOM + BAR_H / 2)
        glyph = _icon(icon_name)
        text_x = self._x(PAD)
        if glyph:
            self._text(text_x, mid, glyph, ICON_FONT_BOLD, 64, fg)
            text_x += (64 + 26) * k
        room = self._x(DESIGN_W - PAD) - text_x
        self._text(text_x, mid - 17 * k, _ellipsize(self._font(SEMI_HEAVY, 48), title, room), SEMI_HEAVY, 48, fg)
        self._text(text_x, mid + 25 * k, _ellipsize(self._font(FONT, 24), sub, room), FONT, 24, fg)
