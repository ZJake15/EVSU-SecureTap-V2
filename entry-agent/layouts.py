"""The gate monitor's screen layouts - the "Layout" menu in its status bar
(docs/Redesign UI/Gate Monitor.dc.html):

    Split            camera and live log side by side (the everyday default)
    Verify wall      the 4 newest people as photo-on-file vs live-face cards,
                     the camera in a corner (click it to swap)
    Full camera      the video edge to edge, stats / newest person / earlier
                     people floating on top of it
    Filmstrip        camera on top, a row of person cards below
    Entrance + Exit  both cameras (side by side, or big + small), one merged
                     log tagged IN / OUT
    Compact rail     camera first, the log as a column of round avatars
    Separate window  Split, with the exit camera in its own window (2 monitors)

Each layout is a view that builds its widgets inside the window's body frame
and is rebuilt from scratch when the guard picks another one. They share the
window's data - its live log (GateMonitorWindow._log_entries, newest first),
counters and alarm state - and its camera panels (ui._Feed), which a view
moves into its own frames. With an exit camera, the one-camera layouts show
it as a small inset in a corner of the entrance camera (click to swap).
"""

import io
import tkinter as tk

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageOps, ImageTk

from ui import (
    ALERTS, BRASS, CANVAS, CAUTION, COND_BOLD, COND_HEAVY, DANGER, FONT, FONT_MONO, FONT_MONO_MEDIUM,
    FONT_MONO_SEMIBOLD, ICON_FONT_BOLD, INK, INK_400, INK_600, KIND_COLORS, LINE, LOG_KINDS, SEMI_HEAVY, SURFACE,
    VERIFIED, WIDE_HEAVY, _ellipsize, _glyph_tile, _icon, _initials_avatar, _pil_font, _rounded_rect_points,
    details_line,
)

INK_RGB = tuple(int(INK[i:i + 2], 16) for i in (1, 3, 5))

# Kinds with no person on record behind them - their "photo" is the camera's
# own crop (or an icon), and they have no photo on file.
NO_RECORD = {"unknown", "spoof", "covered", "card_rejected", "card_offline"}

# The thin stats row (Verify wall, Filmstrip): (key, label, color, icon).
THIN_STATS = (
    ("today", "TODAY", INK, "calendar-blank"),
    ("entries", "ENTRIES", INK, "sign-in"),
    ("exits", "EXITS", INK, "sign-out"),
    ("unknown", "UNKNOWN", CAUTION, "user-circle-dashed"),
    ("spoof", "SPOOF", DANGER, "warning-octagon"),
    ("in_frame", "IN FRAME", INK_600, "users"),
)


# ---- small helpers -------------------------------------------------------------


def hm(entry):
    """"07:42" from "07:42:18 AM"."""
    return entry["time"][:5]


def short_name(entry):
    """"Maria S." for a person; the event's own name otherwise."""
    if entry["kind"] in NO_RECORD:
        return {"unknown": "Unknown", "spoof": "Possible spoof", "covered": "Face covered"}.get(
            entry["kind"], entry["name"])
    parts = (entry["name"] or "").split()
    return f"{parts[0]} {parts[-1][0]}." if len(parts) > 1 else (entry["name"] or "—")


def note_of(entry):
    """The line under a name: school · department · role, or what the event
    means when there's no person on record."""
    kind = entry["kind"]
    if kind == "unknown":
        return "Not recognized — check at the gate"
    if kind == "spoof":
        return "Photo or screen held to the camera"
    if kind == "covered":
        return "Asked to uncover their face"
    if kind == "card_rejected":
        return "Card is not registered"
    if kind == "card_offline":
        return "Saved — sends when the server is back"
    return details_line(entry.get("details"))


def color_of(entry):
    return LOG_KINDS[entry["kind"]]["color"]


def tint_of(entry):
    view = LOG_KINDS[entry["kind"]]
    return view.get("tint", SURFACE) if view.get("sec") else SURFACE


def cached(entry, key, make):
    """An image made once per entry and size (Tk also needs it referenced)."""
    images = entry.setdefault("_img", {})
    if key not in images:
        images[key] = make()
    return images[key]


def entry_pil(entry, width, height):
    """The entry's picture at width x height real pixels: the photo on file
    (or the camera's crop for an unknown face), else an initials or icon tile."""
    raw = entry.get("photo_bytes")
    if raw:
        try:
            return ImageOps.fit(Image.open(io.BytesIO(raw)).convert("RGB"), (width, height), Image.LANCZOS)
        except Exception:
            pass
    side = max(width, height)
    kind = entry["kind"]
    if kind in ("unknown", "spoof"):
        tile = _glyph_tile(side, INK, "user-circle-dashed", INK_400)
    elif kind == "covered":
        tile = _glyph_tile(side, INK, "hand-palm", INK_400)
    elif kind == "card_rejected":
        tile = _glyph_tile(side, DANGER, "identification-card", SURFACE, bold=True)
    elif kind == "card_offline":
        tile = _glyph_tile(side, CAUTION, "cloud-slash", SURFACE, bold=True)
    else:
        tile = _initials_avatar(entry.get("initials_seed") or entry.get("name"), side)
    return ImageOps.fit(tile.convert("RGB"), (width, height))


def live_pil(entry, width, height):
    """The face as the camera saw it: the crop taken when the event was
    logged (GateMonitorWindow._crop_live_face), or the backend's crop of an
    unknown face - None for a card tap."""
    live = entry.get("live_image")
    if live is None and entry["kind"] in NO_RECORD and entry.get("photo_bytes"):
        return entry_pil(entry, width, height)
    if live is None:
        return None
    return ImageOps.fit(live.convert("RGB"), (width, height), Image.LANCZOS)


def ctk_image(pil, width, height):
    return ctk.CTkImage(light_image=pil, dark_image=pil, size=(width, height))


def round_rect(canvas, x0, y0, x1, y1, radius, fill):
    return canvas.create_polygon(_rounded_rect_points(x0, y0, x1, y1, radius), fill=fill, outline="")


def role_line(m, parent, entry, font_px, bg, max_px, tag_px=12):
    """STUDENT / STAFF tag + "school · department · role" (or the event's note)
    in one row - None when there's nothing to say."""
    details = entry.get("details") or {}
    kind = details.get("kind") if entry["kind"] not in NO_RECORD else None
    text = note_of(entry)
    if not kind and not text:
        return None
    row = ctk.CTkFrame(parent, fg_color=bg, corner_radius=0)
    used = 0
    if kind:
        tag = m._label(row, f" {kind.upper()} ", (COND_HEAVY, tag_px), "white",
                       fg_color=KIND_COLORS.get(kind, INK_600), corner_radius=2, height=tag_px + 8)
        tag.pack(side="left", padx=(0, 8))
        used = m._tkfont(COND_HEAVY, tag_px).measure(f" {kind.upper()} ") + m._px(16)
    if text:
        font = m._tkfont(FONT, font_px)
        m._label(row, _ellipsize(font, text, max(m._px(40), max_px - used)), (FONT, font_px), INK_600,
                 anchor="w", fg_color=bg).pack(side="left", fill="x")
    return row


def word_label(m, parent, entry, px, bg, word=None):
    """The event's icon + word in its status color (ENTRY, UNKNOWN, ...)."""
    view = LOG_KINDS[entry["kind"]]
    box = ctk.CTkFrame(parent, fg_color=bg, corner_radius=0)
    m._icon_label(box, view["icon"], px, view["color"], fg_color=bg).pack(side="left", padx=(0, 6))
    m._label(box, word or entry["word"], (COND_HEAVY, px), view["color"], fg_color=bg).pack(side="left")
    return box


def bind_tree(widget, sequence, handler):
    """Binds every widget in a little tree - a click anywhere on a card."""
    widget.bind(sequence, handler, add="+")
    for child in widget.winfo_children():
        bind_tree(child, sequence, handler)


# ---- shared pieces -----------------------------------------------------------------


class BannerBar:
    """The alarm as a bar of its own above the content - for the layouts whose
    camera is small or shown twice (Verify wall, Entrance + Exit); the others
    show it in the camera's own top strip."""

    def __init__(self, m, parent, grid, name_camera=False):
        s = m._s
        self.name_camera = name_camera
        self.frame = ctk.CTkFrame(parent, fg_color=CAUTION, corner_radius=8, height=s["banner_h"])
        self.frame.grid(**grid)
        self.frame.pack_propagate(False)
        self.icon = m._icon_label(self.frame, "warning", s["banner_i"], "white")
        self.icon.pack(side="left", padx=(20, 14))
        self.title = m._label(self.frame, "", (SEMI_HEAVY, s["banner_t"]), "white")
        self.title.pack(side="left", padx=(0, 14))
        self.time = m._label(self.frame, "", (FONT_MONO_MEDIUM, 16), "white")
        self.time.pack(side="right", padx=(0, 20))
        m._icon_label(self.frame, "speaker-high", 24, "white").pack(side="right", padx=(14, 14))
        self.sub = m._label(self.frame, "", (FONT, s["banner_s"]), "white", anchor="w")
        self.sub.pack(side="left", fill="x", expand=True)
        self.frame.grid_remove()

    def update(self, alert):
        if not alert:
            self.frame.grid_remove()
            return
        spec = ALERTS[alert["kind"]]
        self.frame.configure(fg_color=spec["color"])
        self.icon.configure(text=_icon(spec["icon"]))
        self.title.configure(text=spec["title"])
        sub = alert.get("sub") or spec["sub"]
        if self.name_camera:
            sub = ("Exit camera — " if alert.get("direction") == "exit" else "Entrance camera — ") + sub
        self.sub.configure(text=sub)
        self.time.configure(text=alert["time"])
        self.frame.grid()


class _View:
    """One layout: builds itself in `parent`, and is told when the log, the
    counters or the alarm change. Rebuilt from scratch on a layout change."""

    is_split = False

    def __init__(self, m, parent):
        self.m = m
        self.s = m._s
        self.stat_labels = {}
        self.frame = ctk.CTkFrame(parent, fg_color="transparent")
        self.frame.pack(fill="both", expand=True)
        self.build()
        self.refresh_log()
        self.refresh_stats()

    def build(self):
        raise NotImplementedError

    # Called by the window ------------------------------------------------------

    def refresh_log(self):
        """The whole live log changed (built, or an Unknown undone)."""

    def on_new_entry(self, previous):
        """A new event is at the top of the log; `previous` was the newest."""
        self.refresh_log()

    def on_entries_removed(self, removed, featured_before):
        self.refresh_log()

    def refresh_stats(self):
        """Every counter shown - each label touched only when its number
        changed (this runs on every scan answer, ~5 times a second per
        camera, and redrawing an unchanged CTk label still costs)."""
        shown = self.__dict__.setdefault("_shown_stats", {})
        for key, label in self.stat_labels.items():
            text = self.m._stat_text(key)
            if shown.get(key) != text:
                shown[key] = text
                label.configure(text=text)

    def on_alert(self, alert):
        """The alarm banner started or ended (None)."""

    def destroy(self):
        self.frame.destroy()

    # Helpers -------------------------------------------------------------------

    def padded(self):
        s = self.s
        box = ctk.CTkFrame(self.frame, fg_color="transparent")
        box.pack(fill="both", expand=True, padx=s["pad"], pady=(s["pad_t"], s["pad_b"]))
        return box

    def thin_stats(self, parent):
        m, s = self.m, self.s
        row = ctk.CTkFrame(parent, fg_color="transparent")
        for key, label, color, icon in THIN_STATS:
            cell = ctk.CTkFrame(row, fg_color="transparent")
            cell.pack(side="left", padx=(0, s["thin_gap"]))
            head_color = INK_600 if color == INK else color
            m._icon_label(cell, icon, s["stat_label"], head_color).pack(side="left", anchor="s", pady=(0, 4))
            m._label(cell, label, (COND_BOLD, s["stat_label"]), head_color).pack(side="left", anchor="s",
                                                                                padx=(4, 8), pady=(0, 3))
            value = m._label(cell, "0", (WIDE_HEAVY, s["thin_v"]), color, height=round(s["thin_v"] * 1.1))
            value.pack(side="left", anchor="s")
            self.stat_labels[key] = value
        return row

    def mount_feeds(self, box, main_options=None, inset_side="ne", inset_offset=None, inset_scale=0.75):
        """The camera(s) in `box`: the big one filling it and, with an exit
        camera, the other as a small 16:9 inset in a corner - top-right, just
        under the camera's LIVE strip, so it never hides the card pill
        (bottom-right). A click on the inset swaps them
        (GateMonitorWindow._inset_big)."""
        if inset_offset is None:
            inset_offset = self.s["strip"]
        m, s = self.m, self.s
        has_exit = "exit" in m._feeds
        big = m._inset_big if has_exit else "entry"
        options = dict(main_options or {})
        options.setdefault("show_tap", big == "entry")
        main = m._attach_feed(big, box, **options)
        main.canvas.place(relx=0, rely=0, relwidth=1, relheight=1)
        if not has_exit:
            return main, None
        small = "exit" if big == "entry" else "entry"
        inset = m._attach_feed(small, box, compact=True, show_tap=False, background=INK)  # over the video
        inset.on_click = self.swap_inset
        pad = m._px(14)
        anchor = {"se": dict(relx=1, rely=1, x=-pad, y=-pad - m._px(inset_offset), anchor="se"),
                  "ne": dict(relx=1, rely=0, x=-pad, y=pad + m._px(inset_offset), anchor="ne")}[inset_side]
        inset.canvas.place(width=m._px(s["inset_w"] * inset_scale), height=m._px(s["inset_h"] * inset_scale),
                           **anchor)
        inset.canvas.tk.call("raise", inset.canvas._w)  # above the big one (Canvas.lift raises items)
        return main, inset

    def swap_inset(self):
        m = self.m
        m._inset_big = "exit" if m._inset_big == "entry" else "entry"
        m._apply_layout()


# ---- Split (and Separate window) -------------------------------------------------------


class SplitView(_View):
    """The everyday default: stats and the camera on the left, the live log -
    the newest event as a big card, everything earlier listed below - on the
    right, 6:5."""

    is_split = True

    def build(self):
        m, s = self.m, self.s
        area = self.padded()
        area.grid_rowconfigure(0, weight=1)
        area.grid_columnconfigure(0, weight=6, uniform="split")
        area.grid_columnconfigure(1, weight=5, uniform="split")
        left = ctk.CTkFrame(area, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, s["col_gap"]))
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(1, weight=1)
        m._left_column = left
        m._build_stats_strip(left)
        box = ctk.CTkFrame(left, fg_color="transparent")
        box.grid(row=1, column=0, sticky="nsew")
        self.mount_camera(box)
        m._build_log_panel(area)
        left.bind("<Configure>", m._on_left_configure)
        self.stat_labels = m.stat_tiles

    def mount_camera(self, box):
        self.mount_feeds(box)

    def refresh_log(self):
        self.m._rebuild_log_list()

    def on_new_entry(self, previous):
        m = self.m
        m._show_featured_entry()
        if previous is not None and previous in m._log_entries:
            m._add_log_row(previous)
        m._refresh_log_placeholder()

    def on_entries_removed(self, removed, featured_before):
        m = self.m
        for entry in removed:
            m._destroy_log_row(entry)
        if m._log_entries and m._log_entries[0] is not featured_before:
            m._destroy_log_row(m._log_entries[0])  # moves up from the list into the featured card
        m._show_featured_entry()
        m._refresh_log_placeholder()


class SeparateView(SplitView):
    """Split, with the exit camera in its own window - fullscreen on a second
    monitor when there is one (ui._ExitWindow), with its own exit log."""

    def mount_camera(self, box):
        m = self.m
        feed = m._attach_feed("entry", box, show_tap=True)
        feed.canvas.place(relx=0, rely=0, relwidth=1, relheight=1)
        if "exit" not in m._feeds:
            return
        if m._exit_window is None:
            from ui import _ExitWindow

            m._exit_window = _ExitWindow(m, on_close=m._exit_window_closed)
            m._refresh_exit_counts()
        exit_feed = m._attach_feed("exit", m._exit_window.feed_area, show_tap=False)
        exit_feed.canvas.grid(row=0, column=0, sticky="nsew")
        exit_feed.canvas.bind("<Double-Button-1>", lambda _e: m._exit_window.toggle_fullscreen())


# ---- Verify wall ---------------------------------------------------------------------


class VerifyView(_View):
    """For checking faces: the 4 newest people as cards - the photo on file
    next to the face the camera saw - with the camera in a corner. A click on
    the corner shows that camera big instead, with the newest person in the
    corner (click again for the cards)."""

    def build(self):
        m, s = self.m, self.s
        box = self.padded()
        box.grid_columnconfigure(0, weight=1)
        box.grid_rowconfigure(2, weight=1)
        self.thin_stats(box).grid(row=0, column=0, sticky="w")
        self.banner = BannerBar(m, box, dict(row=1, column=0, sticky="ew", pady=(s["gap"], 0)))
        self.content = ctk.CTkFrame(box, fg_color="transparent")
        self.content.grid(row=2, column=0, sticky="nsew", pady=(s["gap"], 0))
        self.swapped = m._verify_swap if m._verify_swap in m._feeds else None
        if self.swapped:
            feed = m._attach_feed(self.swapped, self.content, show_banner=False, show_tap=self.swapped == "entry")
            feed.canvas.place(relx=0, rely=0, relwidth=1, relheight=1)
        else:
            self.content.grid_columnconfigure((0, 1), weight=1, uniform="vcol")
            self.content.grid_rowconfigure((0, 1), weight=1, uniform="vrow")
        self.cards = []
        self.build_pips()

    def build_pips(self):
        """The corner box(es), bottom-right over everything: each camera
        small - or, with a camera shown big, the newest person."""
        m, s = self.m, self.s
        right, bottom = s["pad"] + 12, s["pad_b"] + 12
        self.pip_mini = None
        # With one camera shown big, the newest person takes its corner - and
        # the other camera (if any) keeps its own.
        pips = (["card"] if self.swapped else []) + [d for d in ("entry", "exit")
                                                      if d in m._feeds and d != self.swapped]
        for slot, what in enumerate(pips):
            frame = ctk.CTkFrame(self.frame, fg_color=SURFACE, corner_radius=10, width=s["pip_w"],
                                 height=s["pip_h"])
            frame.place(relx=1, rely=1, x=-m._px(right + slot * (s["pip_w"] + 14)), y=-m._px(bottom), anchor="se")
            frame.pack_propagate(False)
            if what == "card":
                self.pip_mini = ctk.CTkFrame(frame, fg_color=SURFACE, corner_radius=0)
                self.pip_mini.pack(fill="both", expand=True, padx=3, pady=3)
            else:
                feed = m._attach_feed(what, frame, compact=True, show_tap=False, background=SURFACE)
                feed.canvas.pack(fill="both", expand=True, padx=3, pady=3)
                feed.on_click = (lambda d=what: self.show_camera(d))
            swap = m._icon_label(frame, "arrows-left-right", 15, INK, fg_color=SURFACE, corner_radius=6,
                                 width=26, height=26, cursor="hand2")
            swap.place(relx=1, x=-m._px(6), y=m._px(6), anchor="ne")
            swap.bind("<Button-1>", lambda _e, w=what: self.show_cards() if w == "card" else self.show_camera(w))

    def show_camera(self, direction):
        self.m._verify_swap = direction
        self.m._apply_layout()

    def show_cards(self):
        self.m._verify_swap = None
        self.m._apply_layout()

    def refresh_log(self):
        m = self.m
        if self.swapped:
            self.fill_mini()
            return
        for card in self.cards:
            card.destroy()
        self.cards = []
        entries = m._log_entries[:4]
        if not entries:
            empty = m._label(self.content, "Waiting for the first scan or tap…", (FONT, 16), INK_600, anchor="w")
            empty.grid(row=0, column=0, sticky="nw")
            self.cards.append(empty)
        for index, entry in enumerate(entries):
            card = self.card(entry)
            row, column = divmod(index, 2)
            card.grid(row=row, column=column, sticky="nsew", padx=(0, self.s["gap"]) if column == 0 else 0,
                      pady=(0, self.s["gap"]) if row == 0 else 0)
            self.cards.append(card)

    def card(self, entry):
        m, s = self.m, self.s
        color, bg = color_of(entry), tint_of(entry)
        card = ctk.CTkFrame(self.content, fg_color=SURFACE, corner_radius=8, border_width=1, border_color=LINE)
        card.grid_propagate(False)
        card.pack_propagate(False)
        head = ctk.CTkFrame(card, fg_color=color, corner_radius=0, height=s["v_head"])
        head.pack(fill="x", padx=1, pady=(1, 0))
        head.pack_propagate(False)
        m._icon_label(head, LOG_KINDS[entry["kind"]]["icon"], s["v_head_t"], "white", fg_color=color).pack(
            side="left", padx=(14, 10))
        m._label(head, entry["time"], (FONT_MONO_MEDIUM, s["v_time"]), "white", fg_color=color).pack(
            side="right", padx=(0, 14))
        if entry.get("conf") is not None:
            m._label(head, f"{entry['conf']}%", (FONT_MONO_SEMIBOLD, s["v_head_t"]), "white",
                     fg_color=color).pack(side="right", padx=(0, 14))
            m._label(head, "MATCH", (COND_BOLD, 12), "white", fg_color=color).pack(side="right", padx=(0, 6))
        m._label(head, entry["feature_word"], (COND_HEAVY, s["v_head_t"]), "white", fg_color=color,
                 anchor="w").pack(side="left", fill="x", expand=True)
        body = ctk.CTkFrame(card, fg_color=bg, corner_radius=0)
        body.pack(fill="both", expand=True, padx=1, pady=(0, 1))
        body.grid_columnconfigure(2, weight=1)
        width, height = s["v_tile_w"], s["v_tile_h"]
        real = (m._px(width), m._px(height))
        on_file = cached(entry, ("file", real), lambda: self.file_tile(entry, *real))
        live = cached(entry, ("live", real), lambda: self.live_tile(entry, *real))
        for column, (label, image) in enumerate((("ON FILE", on_file), ("LIVE", live))):
            cell = ctk.CTkFrame(body, fg_color=bg, corner_radius=0)
            cell.grid(row=0, column=column, sticky="nw", padx=(s["v_pad"] if column == 0 else 10, 0),
                      pady=s["v_pad"])
            m._label(cell, label, (COND_BOLD, 12), INK_600, anchor="w", fg_color=bg).pack(anchor="w")
            m._label(cell, "", image=ctk_image(image, width, height), fg_color=bg).pack(anchor="w", pady=(4, 0))
        text = ctk.CTkFrame(body, fg_color=bg, corner_radius=0)
        text.grid(row=0, column=2, sticky="new", padx=(s["v_gap"], s["v_pad"]), pady=(s["v_pad"] + 18, s["v_pad"]))
        wrap = m._px(250 if width > 150 else 150)
        m._label(text, entry["name"], (FONT, s["v_name"], "bold"), INK, anchor="w", justify="left",
                 wraplength=wrap, fg_color=bg).pack(fill="x", anchor="w")
        details = entry.get("details") or {}
        if details.get("kind") and entry["kind"] not in NO_RECORD:
            m._label(text, f" {details['kind'].upper()} ", (COND_HEAVY, 13), "white",
                     fg_color=KIND_COLORS.get(details["kind"], INK_600), corner_radius=2, height=21).pack(
                anchor="w", pady=(8, 0))
        note = note_of(entry)
        if note:
            m._label(text, note, (FONT, s["v_dept"]), INK_600, anchor="w", justify="left", wraplength=wrap,
                     fg_color=bg).pack(fill="x", anchor="w", pady=(8, 0))
        m._label(text, entry["id_text"], (FONT_MONO_MEDIUM, s["v_id"]), INK, anchor="w", fg_color=bg).pack(
            fill="x", anchor="w", pady=(8, 0))
        if entry.get("note"):
            m._label(text, entry["note"], (FONT, s["v_dept"], "bold"), CAUTION, anchor="w", justify="left",
                     wraplength=wrap, fg_color=bg).pack(fill="x", anchor="w", pady=(6, 0))
        return card

    @staticmethod
    def file_tile(entry, width, height):
        """The photo on file - or a dashed "No record" box."""
        if entry["kind"] not in NO_RECORD:
            return entry_pil(entry, width, height)
        image = Image.new("RGB", (width, height), SURFACE)
        draw = ImageDraw.Draw(image)
        dash, gap, line = 8, 6, 2
        for x in range(0, width, dash + gap):
            draw.line((x, 0, min(width, x + dash), 0), fill=INK_400, width=line)
            draw.line((x, height - 1, min(width, x + dash), height - 1), fill=INK_400, width=line)
        for y in range(0, height, dash + gap):
            draw.line((0, y, 0, min(height, y + dash)), fill=INK_400, width=line)
            draw.line((width - 1, y, width - 1, min(height, y + dash)), fill=INK_400, width=line)
        font = _pil_font("SecureTap-Semi-Bold.ttf", max(10, width // 12))
        box = draw.textbbox((0, 0), "No record", font=font)
        draw.text(((width - box[2] + box[0]) / 2 - box[0], (height - box[3] + box[1]) / 2 - box[1]), "No record",
                  fill=INK_600, font=font)
        return image

    @staticmethod
    def live_tile(entry, width, height):
        """The face the camera saw, framed in the event's color."""
        image = live_pil(entry, width, height)
        if image is None:
            image = ImageOps.fit(_glyph_tile(max(width, height), INK_600, "user", INK_400, scale=0.7,
                                             bold=True).convert("RGB"), (width, height))
        border = max(2, round(width / 60))
        ImageDraw.Draw(image).rectangle((0, 0, width - 1, height - 1), outline=color_of(entry), width=border)
        return image

    def fill_mini(self):
        """The corner box while a camera is shown big: the newest person."""
        m, s = self.m, self.s
        frame = self.pip_mini
        if frame is None:
            return
        for child in frame.winfo_children():
            child.destroy()
        if not m._log_entries:
            m._label(frame, "No events yet", (FONT, 14), INK_600).pack(expand=True)
            bind_tree(frame, "<Button-1>", lambda _e: self.show_cards())
            return
        entry = m._log_entries[0]
        color = color_of(entry)
        strip = ctk.CTkFrame(frame, fg_color=color, corner_radius=0, height=s["pip_strip"])
        strip.pack(fill="x")
        strip.pack_propagate(False)
        m._icon_label(strip, LOG_KINDS[entry["kind"]]["icon"], 14, "white", fg_color=color).pack(
            side="left", padx=(10, 6))
        m._label(strip, entry["word"], (COND_HEAVY, 13), "white", fg_color=color).pack(side="left")
        body = ctk.CTkFrame(frame, fg_color=SURFACE, corner_radius=0)
        body.pack(fill="both", expand=True)
        size = s["pip_photo"]
        photo = cached(entry, ("sq", m._px(size)), lambda: entry_pil(entry, m._px(size), m._px(size)))
        m._label(body, "", image=ctk_image(photo, size, size)).pack(side="left", padx=10)
        text = ctk.CTkFrame(body, fg_color=SURFACE, corner_radius=0)
        text.pack(side="left", fill="x", expand=True)
        m._label(text, entry["name"], (FONT, 16, "bold"), INK, anchor="w", justify="left",
                 wraplength=m._px(s["pip_w"] - size - 50)).pack(fill="x", anchor="w")
        m._label(text, "Click for all 4 cards", (FONT, 12), INK_600, anchor="w").pack(fill="x", anchor="w")
        bind_tree(frame, "<Button-1>", lambda _e: self.show_cards())

    def on_alert(self, alert):
        self.banner.update(alert)


# ---- Full camera -----------------------------------------------------------------------


class FullOverlay:
    """What the Full camera layout floats over the video, painted into the
    camera panel each frame (ui._Feed calls paint, then draw): a see-through
    stats bar along the top, the alarm under it, the newest person bottom-left
    and earlier people as a ticker along the bottom."""

    def __init__(self, m):
        self.m = m
        self.photos = {}  # (id(entry), size, ring) -> PhotoImage, kept while shown

    def card_box(self, rect):
        m, s = self.m, self.m._s
        x0 = rect[0] + m._px(s["pad"])
        y1 = rect[1] + rect[3] - m._px(s["tick_h"] + 16)
        return (x0, y1 - m._px(s["f_photo"] + 32), x0 + m._px(s["f_card_w"]), y1)

    def paint(self, feed, panel):
        """See-through dark bands, blended into the picture itself (a Tk
        canvas has no transparency). `panel` is the frame's RGB pixels
        (numpy) - darkened in place with OpenCV: pixel * (1 - a) + ink * a."""
        import cv2

        m, s = self.m, self.m._s
        height, width = panel.shape[:2]
        boxes = [((0, 0, width, m._px(s["f_top"])), 0.74),
                 ((0, height - m._px(s["tick_h"]), width, height), 0.84)]
        if m._log_entries:
            boxes.append((tuple(round(v) for v in self.card_box((0, 0, width, height))), 0.8))
        ink = sum(INK_RGB) / 3  # the ink is a near-neutral dark grey
        for (x0, y0, x1, y1), alpha in boxes:
            x0, y0, x1, y1 = max(0, x0), max(0, y0), min(width, x1), min(height, y1)
            if x1 <= x0 or y1 <= y0:
                continue
            region = panel[y0:y1, x0:x1]
            region[:] = cv2.convertScaleAbs(region, alpha=1 - alpha, beta=ink * alpha)

    def photo(self, entry, size, ring=None):
        key = (id(entry), size, ring)
        if key not in self.photos:
            image = entry_pil(entry, size, size)
            if ring:
                ImageDraw.Draw(image).rectangle((0, 0, size - 1, size - 1), outline=ring, width=max(2, size // 20))
            self.photos[key] = ImageTk.PhotoImage(image)
        return self.photos[key]

    def draw(self, feed, rect):
        m, s = self.m, self.m._s
        canvas, px = feed.canvas, m._px
        x, y, width, _height = rect
        # Top bar: LIVE ENTRANCE, the counters, the camera caption.
        cy = y + px(s["f_top"]) / 2
        cx = x + px(s["pad"])
        live = not feed.showing_no_camera
        dot = px(10)
        canvas.create_rectangle(cx, cy - dot / 2, cx + dot, cy + dot / 2, fill=BRASS if live else INK_400,
                                outline="")
        item = feed.text(cx + dot + px(8), cy, "LIVE" if live else "NO SIGNAL", COND_HEAVY, s["strip_f"], "white")
        cx = canvas.bbox(item)[2] + px(8)
        item = feed.text(cx, cy, "ENTRANCE" if feed.direction == "entry" else "EXIT", COND_HEAVY, s["strip_f"],
                         BRASS)
        cx = canvas.bbox(item)[2] + px(22)
        for key, label, dot_color in (("today", "TODAY", SURFACE), ("entries", "ENTRIES", SURFACE),
                                      ("exits", "EXITS", SURFACE), ("unknown", "UNKNOWN", BRASS),
                                      ("spoof", "SPOOF", DANGER), ("in_frame", "IN FRAME", INK_400)):
            if cx > x + width - px(300):
                break
            canvas.create_line(cx, cy - px(14), cx, cy + px(14), fill=INK_600)
            cx += px(20)
            canvas.create_rectangle(cx, cy - px(4.5), cx + px(9), cy + px(4.5), fill=dot_color, outline="")
            item = feed.text(cx + px(17), cy, label, COND_BOLD, s["stat_label"], LINE)
            item = feed.text(canvas.bbox(item)[2] + px(8), cy, m._stat_text(key), SEMI_HEAVY, s["f_stat_v"], "white")
            cx = canvas.bbox(item)[2] + px(22)
        right = x + width - px(s["pad"])
        if right - cx > px(80):
            caption = _ellipsize(m._tkfont(FONT_MONO, s["cap"]), feed.caption, right - cx)
            feed.text(right, cy, caption, FONT_MONO, s["cap"], LINE, anchor="e")
        # The alarm, right under the top bar.
        alert = m._alert
        if alert:
            spec = ALERTS[alert["kind"]]
            top, bh = y + px(s["f_top"]), px(s["f_banner"])
            canvas.create_rectangle(x, top, x + width, top + bh, fill=spec["color"], outline="")
            by, bx = top + bh / 2, x + px(s["pad"])
            glyph = _icon(spec["icon"])
            if glyph:
                item = feed.text(bx, by, glyph, ICON_FONT_BOLD, s["f_banner_t"], "white")
                bx = canvas.bbox(item)[2] + px(16)
            prefix = "EXIT · " if alert.get("direction") == "exit" else ""
            item = feed.text(bx, by, prefix + spec["title"], SEMI_HEAVY, s["f_banner_t"], "white")
            bx = canvas.bbox(item)[2] + px(16)
            time_item = feed.text(x + width - px(s["pad"]), by, alert["time"], FONT_MONO_MEDIUM, 16, "white",
                                  anchor="e")
            room = canvas.bbox(time_item)[0] - px(24) - bx
            if room > px(40):
                feed.text(bx, by, _ellipsize(m._tkfont(FONT, s["banner_s"]), alert.get("sub") or spec["sub"], room),
                          FONT, s["banner_s"], "white")
        entries = m._log_entries
        if entries:
            self.draw_card(feed, rect, entries[0])
        self.draw_ticker(feed, rect, entries[1:12])
        shown = {id(entry) for entry in entries[:12]}
        for key in [k for k in self.photos if k[0] not in shown]:
            del self.photos[key]  # people no longer shown

    def draw_card(self, feed, rect, entry):
        m, s = self.m, self.m._s
        canvas, px = feed.canvas, m._px
        x0, y0, x1, _y1 = self.card_box(rect)
        color = color_of(entry)
        canvas.create_rectangle(x0, y0, x0 + px(6), self.card_box(rect)[3], fill=color, outline="")
        size = px(s["f_photo"])
        canvas.create_image(x0 + px(24), y0 + px(16), image=self.photo(entry, size), anchor="nw")
        tx = x0 + px(24) + size + px(18)
        right = x1 - px(18)
        tag_font = m._tkfont(COND_HEAVY, s["tag"])
        word = entry["word"]
        pill_w = tag_font.measure(word) + px(s["tag"] + 26)
        pill_h = px(s["tag"] + 10)
        py = y0 + px(16)
        round_rect(canvas, tx, py, tx + pill_w, py + pill_h, px(3), color)
        glyph = _icon(LOG_KINDS[entry["kind"]]["icon"])
        if glyph:
            feed.text(tx + px(9), py + pill_h / 2, glyph, ICON_FONT_BOLD, s["tag"], "white")
        feed.text(tx + px(s["tag"] + 15), py + pill_h / 2, word, COND_HEAVY, s["tag"], "white")
        feed.text(tx + pill_w + px(10), py + pill_h / 2, entry["time"], FONT_MONO_MEDIUM, s["meta"], LINE)
        if entry.get("conf") is not None:
            feed.text(right, py + pill_h / 2, f"{entry['conf']}%", FONT_MONO_SEMIBOLD, s["f_conf"], "white",
                      anchor="e")
        name_font = m._tkfont(FONT, s["f_name"], "bold")
        item = feed.text(tx, py + pill_h + px(6), _ellipsize(name_font, entry["name"], right - tx), FONT, s["f_name"],
                         "white", anchor="nw", weight="bold")
        ly = canvas.bbox(item)[3] + px(6)
        details = entry.get("details") or {}
        kind = details.get("kind") if entry["kind"] not in NO_RECORD else None
        lx = tx
        if kind:
            tag = f" {kind.upper()} "
            tag_w = m._tkfont(COND_HEAVY, 12).measure(tag) + px(4)
            canvas.create_rectangle(lx, ly, lx + tag_w, ly + px(20), fill=KIND_COLORS.get(kind, INK_600),
                                    outline="")
            feed.text(lx + px(2), ly + px(10), tag, COND_HEAVY, 12, "white")
            lx += tag_w + px(8)
        note = note_of(entry)
        if note:
            feed.text(lx, ly + px(10), _ellipsize(m._tkfont(FONT, s["dept"]), note, right - lx), FONT, s["dept"],
                      LINE)
        id_line = entry["id_text"] + (f" · {entry['note']}" if entry.get("note") else "")
        feed.text(tx, ly + px(26), _ellipsize(m._tkfont(FONT_MONO_MEDIUM, s["meta"]), id_line, right - tx),
                  FONT_MONO_MEDIUM, s["meta"], LINE, anchor="nw")

    def draw_ticker(self, feed, rect, entries):
        m, s = self.m, self.m._s
        canvas, px = feed.canvas, m._px
        x, y, width, height = rect
        cy = y + height - px(s["tick_h"]) / 2
        item = feed.text(x + px(s["pad"]), cy, "EARLIER", COND_BOLD, 13, INK_400)
        cx = canvas.bbox(item)[2] + px(18)
        size = px(s["chip_p"])
        name_font = m._tkfont(FONT, s["chip_f"], "bold")
        word_font, time_font = m._tkfont(COND_HEAVY, 12), m._tkfont(FONT_MONO, 13)
        for entry in entries:
            if cx > x + width:
                break
            color = color_of(entry)
            name = short_name(entry)
            word_w = word_font.measure(entry["word"]) + px(12)
            text_w = max(name_font.measure(name), word_w + px(6) + time_font.measure(hm(entry)))
            chip_w, chip_h = px(16) + size + px(10) + text_w + px(8), size + px(12)
            round_rect(canvas, cx, cy - chip_h / 2, cx + chip_w, cy + chip_h / 2, px(8), INK)
            canvas.create_image(cx + px(6), cy, image=self.photo(entry, size, ring=color), anchor="w")
            tx = cx + px(6) + size + px(10)
            feed.text(tx, cy - px(10), name, FONT, s["chip_f"], "white", weight="bold")
            canvas.create_rectangle(tx, cy + px(2), tx + word_w, cy + px(20), fill=color, outline="")
            feed.text(tx + px(6), cy + px(11), entry["word"], COND_HEAVY, 12, "white")
            feed.text(tx + word_w + px(6), cy + px(11), hm(entry), FONT_MONO, 13, LINE)
            cx += chip_w + px(12)


class FullView(_View):
    """For watching the crowd: the video edge to edge, everything else
    floating on top of it (FullOverlay)."""

    def build(self):
        s = self.s
        self.overlay = FullOverlay(self.m)
        box = ctk.CTkFrame(self.frame, fg_color=INK, corner_radius=0)
        box.pack(fill="both", expand=True)
        self.mount_feeds(box, dict(show_strip=False, show_banner=False, overlay=self.overlay, radius=0,
                                   tap_bottom=s["tick_h"] + 16, background=INK),
                         inset_side="ne", inset_offset=s["f_top"] + s["f_banner"] + 16, inset_scale=1)


# ---- Filmstrip -------------------------------------------------------------------------


class FilmView(_View):
    """For wide monitors and TVs: the camera on top, people as a row of tall
    cards below it, newest on the left."""

    def build(self):
        s = self.s
        box = self.padded()
        box.grid_columnconfigure(0, weight=1)
        box.grid_rowconfigure(1, weight=1)
        self.thin_stats(box).grid(row=0, column=0, sticky="w")
        feed_box = ctk.CTkFrame(box, fg_color="transparent")
        feed_box.grid(row=1, column=0, sticky="nsew", pady=s["gap"])
        self.mount_feeds(feed_box)
        self.strip = ctk.CTkFrame(box, fg_color="transparent", height=s["film_h"])
        self.strip.grid(row=2, column=0, sticky="ew")
        self.strip.grid_propagate(False)
        self.cards = []  # the row runs on past the right edge - there's always more

    def refresh_log(self):
        m, s = self.m, self.s
        for card in self.cards:
            card.destroy()
        self.cards = []
        entries = m._log_entries[: (9 if s["film_w"] > 180 else 8)]
        if not entries:
            empty = m._label(self.strip, "Waiting for the first scan or tap…", (FONT, 16), INK_600)
            empty.place(x=0, y=0)
            self.cards.append(empty)
        for index, entry in enumerate(entries):
            card = self.card(entry)
            card.place(x=m._px(index * (s["film_w"] + s["film_gap"])), y=0)
            self.cards.append(card)

    def card(self, entry):
        m, s = self.m, self.s
        color, bg = color_of(entry), tint_of(entry)
        edge = color if LOG_KINDS[entry["kind"]].get("sec") else LINE
        card = ctk.CTkFrame(self.strip, fg_color=bg, corner_radius=8, border_width=1, border_color=edge,
                            width=s["film_w"], height=s["film_h"])
        card.pack_propagate(False)
        band = ctk.CTkFrame(card, fg_color=color, corner_radius=0, height=s["film_bar"])
        band.pack(fill="x", padx=1, pady=(1, 0))
        band.pack_propagate(False)
        m._icon_label(band, LOG_KINDS[entry["kind"]]["icon"], s["film_tag"], "white", fg_color=color).pack(
            side="left", padx=(10, 6))
        m._label(band, hm(entry), (FONT_MONO, 13), "white", fg_color=color).pack(side="right", padx=(0, 10))
        word_font = m._tkfont(COND_HEAVY, s["film_tag"])
        m._label(band, _ellipsize(word_font, entry["word"], m._px(s["film_w"] - 90)), (COND_HEAVY, s["film_tag"]),
                 "white", fg_color=color, anchor="w").pack(side="left", fill="x", expand=True)
        width, height = s["film_w"] - 2, s["film_photo"]
        real = (m._px(width), m._px(height))
        photo = cached(entry, ("film", real), lambda: entry_pil(entry, *real))
        m._label(card, "", image=ctk_image(photo, width, height), fg_color=bg, corner_radius=0).pack(padx=1)
        text = ctk.CTkFrame(card, fg_color=bg, corner_radius=0)
        text.pack(fill="both", expand=True, padx=10, pady=(8, 6))
        max_px = m._px(s["film_w"] - 22)
        m._label(text, entry["name"], (FONT, s["film_name"], "bold"), INK, anchor="w", justify="left",
                 wraplength=max_px, fg_color=bg).pack(fill="x", anchor="w")
        line = role_line(m, text, entry, 13, bg, max_px, tag_px=11)
        if line is not None:
            line.pack(fill="x", anchor="w", pady=(5, 0))
        id_conf = entry["id_text"] + (f" · {entry['conf']}%" if entry.get("conf") is not None else "")
        m._label(text, _ellipsize(m._tkfont(FONT_MONO_MEDIUM, 13), id_conf, max_px), (FONT_MONO_MEDIUM, 13),
                 INK_600, anchor="w", fg_color=bg).pack(fill="x", anchor="w", pady=(5, 0))
        return card


# ---- Entrance + Exit -----------------------------------------------------------------


class DualView(_View):
    """For two-camera gates: both feeds across the top - side by side, or one
    big with the other as an inset (click to swap) - and one merged log below
    them, every row tagged IN or OUT."""

    def build(self):
        m, s = self.m, self.s
        box = self.padded()
        box.grid_columnconfigure(0, weight=1)
        box.grid_rowconfigure(3, weight=1)
        self.banner = BannerBar(m, box, dict(row=0, column=0, sticky="ew", pady=(0, s["gap"])), name_camera=True)
        self.feeds = ctk.CTkFrame(box, fg_color="transparent", height=s["dual_h"])
        self.feeds.grid(row=1, column=0, sticky="ew")
        self.feeds.grid_propagate(False)
        self.mount()
        head = ctk.CTkFrame(box, fg_color="transparent")
        head.grid(row=2, column=0, sticky="ew", pady=(s["gap"], 8))
        m._label(head, "Live log · both cameras", (SEMI_HEAVY, s["log_title"]), INK).pack(side="left")
        m._label(head, "Newest first", (FONT_MONO, 13), INK_600).pack(side="left", padx=16)
        self.segmented(head).pack(side="right")
        self.rows = ctk.CTkFrame(box, fg_color="transparent")
        self.rows.grid(row=3, column=0, sticky="nsew")
        self.rows.grid_columnconfigure((0, 1), weight=1, uniform="mrow")
        self.row_widgets = []

    def mount(self):
        m, s = self.m, self.s
        half_gap = m._px(s["gap"]) / 2
        if m._dual_mode == "big" and "exit" in m._feeds:
            big = m._inset_big
            small = "exit" if big == "entry" else "entry"
            main = m._attach_feed(big, self.feeds, show_banner=False, show_tap=big == "entry",
                                  strip_stats=self.strip_stats(big))
            main.canvas.place(relx=0, rely=0, relwidth=1, relheight=1)
            # Top-right under the LIVE strip - the card pill has the bottom-right.
            inset = m._attach_feed(small, self.feeds, compact=True, show_banner=False, show_tap=False,
                                   background=INK)
            inset.canvas.place(relx=1, rely=0, x=-m._px(16), y=m._px(s["strip"] + 16), anchor="ne",
                               width=m._px(s["inset_w"]), height=m._px(s["inset_h"]))
            inset.canvas.tk.call("raise", inset.canvas._w)
            inset.on_click = self.swap_inset
            return
        entry = m._attach_feed("entry", self.feeds, show_banner=False, show_tap=True,
                               strip_stats=self.strip_stats("entry"))
        entry.canvas.place(relx=0, rely=0, relwidth=0.5, relheight=1, width=-half_gap)
        if "exit" in m._feeds:
            exit_feed = m._attach_feed("exit", self.feeds, show_banner=False, show_tap=False,
                                       strip_stats=self.strip_stats("exit"))
            exit_feed.canvas.place(relx=0.5, rely=0, relwidth=0.5, relheight=1, x=half_gap, width=-half_gap)
            return
        # A plain Tk frame holds it: CTk widgets refuse a width in place().
        holder = tk.Frame(self.feeds, bg=CANVAS, highlightthickness=0, bd=0)
        holder.place(relx=0.5, rely=0, relwidth=0.5, relheight=1, x=half_gap, width=-half_gap)
        empty = ctk.CTkFrame(holder, fg_color=INK, corner_radius=8)
        empty.pack(fill="both", expand=True)
        m._icon_label(empty, "video-camera-slash", 44, BRASS, bold=False, fg_color=INK).pack(
            expand=True, anchor="s", pady=(0, 10))
        m._label(empty, "No exit camera", (SEMI_HEAVY, 22), "white", fg_color=INK).pack()
        m._label(empty, "Choose one in Cameras & card readers.", (FONT, 15), LINE, fg_color=INK).pack(
            expand=True, anchor="n", pady=(6, 0))

    def strip_stats(self, direction):
        m = self.m

        def in_frame(side):
            feed = m._feeds.get(side)
            return str(len(feed.recognitions)) if feed is not None and not feed.showing_no_camera else "—"

        if direction == "exit":
            return [("EXITS", lambda: m._stat_text("exits"), SURFACE), ("IN FRAME", lambda: in_frame("exit"), INK_400)]
        return [("ENTRIES", lambda: m._stat_text("entries"), SURFACE),
                ("UNKNOWN", lambda: m._stat_text("unknown"), BRASS),
                ("SPOOF", lambda: m._stat_text("spoof"), DANGER),
                ("IN FRAME", lambda: in_frame("entry"), INK_400)]

    def segmented(self, parent):
        """Side by side | Big + small."""
        m = self.m
        frame = ctk.CTkFrame(parent, fg_color=SURFACE, corner_radius=6, border_width=1, border_color=INK_400)
        for index, (mode, icon, text) in enumerate((("side", "columns", "Side by side"),
                                                    ("big", "picture-in-picture", "Big + small"))):
            on = m._dual_mode == mode
            bg, fg = (INK, SURFACE) if on else (SURFACE, INK_600)
            cell = ctk.CTkFrame(frame, fg_color=bg, corner_radius=5, cursor="hand2")
            cell.pack(side="left", padx=(2 if index == 0 else 0, 2), pady=2)
            m._icon_label(cell, icon, 15, fg, fg_color=bg).pack(side="left", padx=(10, 6), pady=4)
            m._label(cell, text, (FONT, 14, "bold"), fg, fg_color=bg).pack(side="left", padx=(0, 12), pady=4)
            bind_tree(cell, "<Button-1>", lambda _e, chosen=mode: m._set_dual_mode(chosen))
        return frame

    def refresh_log(self):
        m, s = self.m, self.s
        for widget in self.row_widgets:
            widget.destroy()
        self.row_widgets = []
        entries = m._log_entries[:8]
        if not entries:
            empty = m._label(self.rows, "Waiting for the first scan or tap…", (FONT, 16), INK_600)
            empty.grid(row=0, column=0, sticky="w")
            self.row_widgets.append(empty)
        for index, entry in enumerate(entries):
            row, column = divmod(index, 2)
            widget = self.row(entry)
            widget.grid(row=row, column=column, sticky="ew", padx=(0, s["gap"]) if column == 0 else 0, pady=(0, 8))
            self.row_widgets.append(widget)

    def row(self, entry):
        m, s = self.m, self.s
        bg = tint_of(entry)
        outer = ctk.CTkFrame(self.rows, fg_color=bg, corner_radius=6, border_width=1, border_color=LINE)
        outer.grid_columnconfigure(3, weight=1)
        bar = color_of(entry) if LOG_KINDS[entry["kind"]].get("sec") else bg
        # height=1: a CTkFrame with nothing in it otherwise asks for 200 px
        # and stretches the whole row.
        ctk.CTkFrame(outer, fg_color=bar, width=3, height=1, corner_radius=0).grid(row=0, column=0, sticky="ns",
                                                                                  padx=(1, 0), pady=6)
        leaving = entry.get("direction") == "exit"
        m._label(outer, "OUT" if leaving else "IN", (SEMI_HEAVY, s["dir_f"]), "white",
                 fg_color=INK if leaving else VERIFIED, corner_radius=4, width=s["dir_w"], height=30).grid(
            row=0, column=1, padx=(10, 12), pady=s["m_pad"])
        size = s["m_row_thumb"]
        photo = cached(entry, ("sq", m._px(size)), lambda: entry_pil(entry, m._px(size), m._px(size)))
        m._label(outer, "", image=ctk_image(photo, size, size), fg_color=bg).grid(row=0, column=2, padx=(0, 14))
        text = ctk.CTkFrame(outer, fg_color=bg, corner_radius=0)
        text.grid(row=0, column=3, sticky="ew", pady=s["m_pad"])
        word_label(m, outer, entry, s["tag"], bg).grid(row=0, column=4, sticky="e", padx=(8, 16))
        max_px = m._px(300 if s["m_row_name"] > 18 else 200)
        name_font = m._tkfont(FONT, s["m_row_name"], "bold")
        m._label(text, _ellipsize(name_font, entry["name"], max_px), (FONT, s["m_row_name"], "bold"), INK,
                 anchor="w", fg_color=bg).pack(fill="x", anchor="w")
        line = role_line(m, text, entry, s["dept"], bg, max_px)
        if line is not None:
            line.pack(fill="x", anchor="w", pady=(3, 0))
        meta_font = m._tkfont(FONT_MONO_MEDIUM, s["meta"])
        m._label(text, _ellipsize(meta_font, m._meta(entry), max_px), (FONT_MONO_MEDIUM, s["meta"]), INK_600,
                 anchor="w", fg_color=bg).pack(fill="x", anchor="w", pady=(3, 0))
        return outer

    def on_alert(self, alert):
        s = self.s
        self.banner.update(alert)
        # The banner takes its room from the cameras, not the log.
        self.feeds.configure(height=s["dual_h"] - (s["banner_h"] + s["gap"] if alert else 0))


# ---- Compact rail ----------------------------------------------------------------------


class RailView(_View):
    """For small laptop screens: the camera takes nearly everything; the log is
    a column of round avatars ringed in their status color - hover or click
    one to see that person's row."""

    def build(self):
        m, s = self.m, self.s
        box = self.padded()
        box.grid_rowconfigure(0, weight=1)
        box.grid_columnconfigure(0, weight=1)
        feed_box = ctk.CTkFrame(box, fg_color="transparent")
        feed_box.grid(row=0, column=0, sticky="nsew", padx=(0, s["gap"]))
        self.mount_feeds(feed_box, dict(strip_stats=[
            ("TODAY", lambda: m._stat_text("today"), SURFACE), ("ENTRIES", lambda: m._stat_text("entries"), SURFACE),
            ("UNKNOWN", lambda: m._stat_text("unknown"), BRASS), ("SPOOF", lambda: m._stat_text("spoof"), DANGER)]))
        self.rail = ctk.CTkFrame(box, fg_color=SURFACE, corner_radius=8, border_width=1, border_color=LINE,
                                 width=s["rail_w"])
        self.rail.grid(row=0, column=1, sticky="ns")
        self.rail.pack_propagate(False)
        m._label(self.rail, "LOG", (COND_BOLD, 13), INK_600).pack(pady=(12, s["rail_gap"] - 4))
        self.avatars = []
        self.popup = None
        self.open_index = 0

    def refresh_log(self):
        m, s = self.m, self.s
        for avatar in self.avatars:
            avatar.destroy()
        self.avatars = []
        size = s["av"]
        for index, entry in enumerate(m._log_entries[: s["rail_n"]]):
            cell = ctk.CTkFrame(self.rail, fg_color=SURFACE, corner_radius=0, cursor="hand2")
            cell.pack(pady=(0, s["rail_gap"]))
            image = cached(entry, ("ring", m._px(size)), lambda e=entry: self.ring_image(e, m._px(size)))
            m._label(cell, "", image=ctk_image(image, size, size), fg_color=SURFACE).pack()
            m._label(cell, hm(entry), (FONT_MONO_MEDIUM, 13), INK_600, fg_color=SURFACE).pack(pady=(4, 0))
            bind_tree(cell, "<Button-1>", lambda _e, i=index: self.open(i))
            bind_tree(cell, "<Enter>", lambda _e, i=index: self.open(i))
            self.avatars.append(cell)
        if self.popup is not None:
            self.popup.destroy()
            self.popup = None
        if self.avatars:
            self.frame.after(50, lambda: self.open(0))

    def ring_image(self, entry, size):
        """A round photo inside a ring of the event's color."""
        ring = max(3, round(size * self.s["ring"] / self.s["av"]))
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.ellipse((0, 0, size - 1, size - 1), fill=color_of(entry))
        draw.ellipse((ring, ring, size - 1 - ring, size - 1 - ring), fill=SURFACE)
        inner = size - 2 * (ring + 3)
        mask = Image.new("L", (inner, inner), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, inner - 1, inner - 1), fill=255)
        image.paste(entry_pil(entry, inner, inner), (ring + 3, ring + 3), mask)
        return image

    def open(self, index):
        """That person's row, to the left of the rail, over the camera."""
        m, s = self.m, self.s
        try:
            if not self.frame.winfo_exists() or index >= len(self.avatars) or index >= len(m._log_entries):
                return
        except Exception:
            return
        if self.popup is not None and self.open_index == index:
            return
        self.open_index = index
        if self.popup is not None:
            self.popup.destroy()
        entry = m._log_entries[index]
        color, bg = color_of(entry), tint_of(entry)
        popup = ctk.CTkFrame(self.frame, fg_color=color, corner_radius=8, width=s["rail_pop_w"])
        inner = ctk.CTkFrame(popup, fg_color=bg, corner_radius=6)
        inner.pack(fill="both", expand=True, padx=(5, 1), pady=1)
        size = s["row_thumb"]
        photo = cached(entry, ("sq", m._px(size)), lambda: entry_pil(entry, m._px(size), m._px(size)))
        m._label(inner, "", image=ctk_image(photo, size, size), fg_color=bg).pack(side="left", padx=(12, 14), pady=12)
        word_label(m, inner, entry, s["tag"], bg).pack(side="right", padx=(8, 16))
        text = ctk.CTkFrame(inner, fg_color=bg, corner_radius=0)
        text.pack(side="left", fill="x", expand=True)
        max_px = m._px(s["rail_pop_w"] - size - 200)
        m._label(text, _ellipsize(m._tkfont(FONT, s["row_name"], "bold"), entry["name"], max_px),
                 (FONT, s["row_name"], "bold"), INK, anchor="w", fg_color=bg).pack(fill="x", anchor="w")
        line = role_line(m, text, entry, s["dept"], bg, max_px)
        if line is not None:
            line.pack(fill="x", anchor="w", pady=(4, 0))
        m._label(text, _ellipsize(m._tkfont(FONT_MONO_MEDIUM, s["meta"]), m._meta(entry), max_px),
                 (FONT_MONO_MEDIUM, s["meta"]), INK_600, anchor="w", fg_color=bg).pack(fill="x", anchor="w",
                                                                                     pady=(4, 0))
        avatar = self.avatars[index]
        y = avatar.winfo_rooty() - self.frame.winfo_rooty() - m._px(8)
        x = self.rail.winfo_rootx() - self.frame.winfo_rootx() - m._px(22)
        popup.place(x=x, y=max(0, y), anchor="ne")
        self.popup = popup


# ---- the menu --------------------------------------------------------------------------


VIEWS = {
    "split": SplitView,
    "verify": VerifyView,
    "full": FullView,
    "film": FilmView,
    "dual": DualView,
    "rail": RailView,
    "separate": SeparateView,
}


def layout_thumb(key, width, height):
    """The Layout menu's little picture of each layout's real shape (the
    design's 92 x 58 thumbnails), at width x height real pixels."""
    image = Image.new("RGB", (width, height), SURFACE)
    d = ImageDraw.Draw(image)
    w, h = width, height
    u = w / 92
    pad = 4 * u
    line_w = max(1, round(2 * u))
    green, amber, red = VERIFIED, CAUTION, DANGER

    def box(x0, y0, x1, y1, fill=None, outline=None, width_=1):
        d.rectangle((round(x0), round(y0), round(x1), round(y1)), fill=fill, outline=outline, width=width_)

    if key in ("split", "separate"):
        split = pad + (w - 2 * pad) * 0.6
        box(pad, pad, split - 2 * u, h - pad, INK_600)
        box(split + 2 * u, pad, w - pad, pad + 13 * u, green)
        rows = (h - pad - (pad + 16 * u)) / 3
        for i in range(3):
            y0 = pad + 16 * u + i * rows
            box(split + 2 * u, y0, w - pad, y0 + rows - 3 * u, LINE)
        if key == "separate":
            box(w - 34 * u, h - 22 * u, w - 3 * u, h - 3 * u, INK, outline=SURFACE, width_=line_w)
            box(w - 30 * u, h - 22 * u, w - 7 * u, h - 18 * u, INK_600)
    elif key == "verify":
        cw, ch = (w - 2 * pad - 3 * u) / 2, (h - 2 * pad - 3 * u) / 2
        for i, top in enumerate((green, green, amber, green)):
            x0 = pad + (i % 2) * (cw + 3 * u)
            y0 = pad + (i // 2) * (ch + 3 * u)
            box(x0, y0, x0 + cw, y0 + ch, CANVAS, outline=LINE)
            box(x0, y0, x0 + cw, y0 + 4 * u, top)
            if i < 3:
                box(x0 + 3 * u, y0 + 7 * u, x0 + 10 * u, y0 + ch - 3 * u, INK_400)
                box(x0 + 12 * u, y0 + 7 * u, x0 + 19 * u, y0 + ch - 3 * u, INK_600)
        box(w - 31 * u, h - 20 * u, w - 3 * u, h - 3 * u, INK, outline=SURFACE, width_=line_w)
    elif key == "full":
        box(0, 0, w, h, INK_600)
        box(0, 0, w, 8 * u, INK)
        box(34 * u, 16 * u, 48 * u, 32 * u, outline=green, width_=line_w)
        box(5 * u, h - 27 * u, 37 * u, h - 15 * u, INK)
        box(5 * u, h - 27 * u, 8 * u, h - 15 * u, green)
        box(0, h - 11 * u, w, h, INK)
        for i in range(6):
            x0 = 4 * u + i * 10 * u
            box(x0, h - 8 * u, x0 + 7 * u, h - 2 * u, BRASS if i == 2 else LINE)
    elif key == "film":
        box(pad, pad, w - pad, h - pad - 21 * u, INK_600)
        cw = (w - 2 * pad - 5 * 3 * u) / 6
        for i, top in enumerate((green, green, amber, green, green, green)):
            x0 = pad + i * (cw + 3 * u)
            box(x0, h - pad - 18 * u, x0 + cw, h - pad, CANVAS, outline=LINE)
            box(x0, h - pad - 18 * u, x0 + cw, h - pad - 15 * u, top)
    elif key == "dual":
        half = (w - 2 * pad - 3 * u) / 2
        box(pad, pad, pad + half, pad + 27 * u, INK_600)
        box(pad, pad, pad + half, pad + 4 * u, green)
        box(pad + half + 3 * u, pad, w - pad, pad + 27 * u, INK_600)
        box(pad + half + 3 * u, pad, w - pad, pad + 4 * u, INK)
        rh = (h - pad - (pad + 30 * u) - 3 * u) / 2
        for i, bar in enumerate((green, INK, green, green)):
            x0 = pad + (i % 2) * (half + 3 * u)
            y0 = pad + 30 * u + (i // 2) * (rh + 3 * u)
            box(x0, y0, x0 + half, y0 + rh, LINE)
            box(x0, y0, x0 + 5 * u, y0 + rh, bar)
    elif key == "rail":
        box(pad, pad, w - pad - 17 * u, h - pad, INK_600)
        cx = w - pad - 7 * u
        for i, ring in enumerate((green, red, amber, green)):
            cy = pad + 6 * u + i * (h - 2 * pad - 12 * u) / 3
            d.ellipse((cx - 5 * u, cy - 5 * u, cx + 5 * u, cy + 5 * u), outline=ring, width=line_w)
    d.rectangle((0, 0, w - 1, h - 1), outline=INK_400, width=max(1, round(u)))
    return image
