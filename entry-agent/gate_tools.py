"""The gate monitor's helper windows:

  Find person        (status bar) look someone up by ID number or name and
                     let them in - or out - by hand: forgot their ID card,
                     face not recognized, card not working. Recorded under
                     the guard on duty, with the reason (backend
                     logs/views.py GateManualEntryView).
  Quick guide        (the "?" in the status bar, after who's on duty) what
                     the colours, tags, sounds and buttons mean - with a
                     play button for each sound.
  Sound menu         (the speaker beside it) how loud the alert sounds
                     are, remembered on this computer, and "Mute for 15
                     minutes" - they come back by themselves.
  New camera         "Use it as the exit camera?" when a second camera is
                     plugged in at a gate without one (main.py).

Each is opened by GateMonitorWindow (ui.py) and uses its fonts, colours and
helpers, the way layouts.py does.
"""

import io
import tkinter as tk

import customtkinter as ctk
from PIL import Image, ImageOps

from alert_sounds import MUTE_MINUTES
from ui import (
    CANVAS, CAUTION, COND_BOLD, DANGER, FONT, FONT_MONO, INK, INK_400, INK_600, LINE, LOG_KINDS, MAROON,
    MAROON_DEEP, PROMPT, SEMI_BOLD, SURFACE, _icon, _initials_avatar, _KeyBurst,
)

WIDTH = 540


def _button(parent, text, command, primary=False, **kwargs):
    style = dict(fg_color=MAROON, hover_color=MAROON_DEEP, text_color="white") if primary else dict(
        fg_color=SURFACE, hover_color=CANVAS, text_color=INK, border_width=1, border_color=INK_400)
    style.update(kwargs)
    style.setdefault("height", 38)
    return ctk.CTkButton(parent, text=text, command=command, font=(FONT, 14, "bold"), corner_radius=8, **style)


def _center_on(window, parent):
    window.update_idletasks()
    x = parent.winfo_rootx() + (parent.winfo_width() - window.winfo_reqwidth()) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - window.winfo_reqheight()) // 3
    window.geometry(f"+{max(0, x)}+{max(0, y)}")
    window.after(150, window.lift)


def _photo(photo_bytes, name, size):
    """A square photo (or initials tile) as a CTkImage."""
    picture = None
    if photo_bytes:
        try:
            picture = ImageOps.fit(Image.open(io.BytesIO(photo_bytes)).convert("RGB"), (size * 2, size * 2))
        except Exception:
            picture = None
    if picture is None:
        picture = _initials_avatar(name or "?", size * 2)
    return ctk.CTkImage(light_image=picture, dark_image=picture, size=(size, size))


# ---- Find person ---------------------------------------------------------------


class FindPersonWindow:
    """search(query, done) and let_in(person, direction, reason, done) are
    main.py's - run off the Tk thread, each calling done(answer) back on it:
    {"results": [...]} / {"ok": True}, or {"error": "..."}."""

    REASONS = ("Forgot ID card", "Face not recognized", "Card not working", "Other")
    SEARCH_PAUSE_MS = 450
    PHOTO = 52

    def __init__(self, monitor, search, let_in, on_close):
        self.m = m = monitor
        self._search = search
        self._let_in = let_in
        self._on_close = on_close
        self._results = []
        self._rows = []
        self._chosen = None
        self._search_job = None
        self._asked = 0  # which search the newest answer must be for
        self._busy = False
        self._images = []

        top = ctk.CTkToplevel(m.window)
        top.title("Find person")
        top.configure(fg_color=SURFACE)
        top.resizable(False, False)
        top.transient(m.window)
        top.protocol("WM_DELETE_WINDOW", self.close)
        top.bind("<Escape>", lambda _e: self.close())
        self.top = top
        m.allow_typing_in(top)  # its text boxes keep the keyboard (ui._keep_focus)

        body = ctk.CTkFrame(top, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=28, pady=22)
        m._label(body, "Find person", (SEMI_BOLD, 22), INK, anchor="w").pack(fill="x")
        m._label(body, "Look someone up by ID number or name - to check who they are, or let them in by hand.",
                 (FONT, 14), INK_600, anchor="w", justify="left", wraplength=WIDTH).pack(fill="x", pady=(2, 12))

        row = ctk.CTkFrame(body, fg_color="transparent")
        row.pack(fill="x")
        self.query = ctk.CTkEntry(row, placeholder_text="ID number or name", font=(FONT, 15), height=40,
                                  corner_radius=6, border_color=INK_400, fg_color=SURFACE, text_color=INK)
        self.query.pack(side="left", fill="x", expand=True)
        self._burst = _KeyBurst(self.query._entry)
        self.query.bind("<Return>", self._enter)
        self.query.bind("<KeyRelease>", self._typed)
        _button(row, "Search", self._search_now, width=96).pack(side="left", padx=(10, 0))

        self.status = m._label(body, "Type at least 2 letters or digits.", (FONT, 13), INK_600, anchor="w",
                               justify="left", wraplength=WIDTH)
        self.status.pack(fill="x", pady=(8, 4))
        self.list = ctk.CTkScrollableFrame(body, fg_color=CANVAS, corner_radius=8, width=WIDTH - 20, height=250)
        self.list.pack(fill="x")

        tk.Frame(body, bg=LINE, height=1).pack(fill="x", pady=(14, 10))
        m._label(body, "LET IN BY HAND - REASON", (COND_BOLD, 14), INK_600, anchor="w").pack(fill="x")
        self.reason = tk.StringVar(value=self.REASONS[0])
        reasons = ctk.CTkFrame(body, fg_color="transparent")
        reasons.pack(fill="x", pady=(6, 0))
        for i, reason in enumerate(self.REASONS):
            ctk.CTkRadioButton(reasons, text=reason, value=reason, variable=self.reason, font=(FONT, 14),
                               text_color=INK, fg_color=MAROON, hover_color=MAROON_DEEP, border_color=INK_400,
                               command=self._reason_changed).grid(row=i // 2, column=i % 2, sticky="w",
                                                                  padx=(0, 24), pady=3)
        self.other = ctk.CTkEntry(body, placeholder_text="What was checked? (e.g. showed a school ID)",
                                  font=(FONT, 14), height=36, corner_radius=6, border_color=INK_400,
                                  fg_color=SURFACE, text_color=INK)
        self.direction = tk.StringVar(value="Entry")
        self._direction_row = ctk.CTkFrame(body, fg_color="transparent", height=1)
        self._direction_row.pack(fill="x")
        if "exit" in m._feeds:  # a gate with an exit camera records people leaving too
            m._label(self._direction_row, "WHICH WAY", (COND_BOLD, 14), INK_600, anchor="w").pack(
                side="left", pady=(10, 0))
            ctk.CTkSegmentedButton(
                self._direction_row, values=["Entry", "Exit"], variable=self.direction, font=(FONT, 14, "bold"),
                height=34, selected_color=MAROON, selected_hover_color=MAROON_DEEP, unselected_color=CANVAS,
                unselected_hover_color=LINE, text_color=INK, command=lambda _v: self._refresh_button(),
            ).pack(side="left", padx=(14, 0), pady=(10, 0))

        self.duty = m._label(body, "", (FONT, 13, "bold"), INK_600, anchor="w", justify="left", wraplength=WIDTH)
        self.duty.pack(fill="x", pady=(12, 0))
        buttons = ctk.CTkFrame(body, fg_color="transparent")
        buttons.pack(fill="x", pady=(12, 0))
        self.let_in_button = _button(buttons, "Let in", self._let_in_now, primary=True, width=150)
        self.let_in_button.pack(side="left")
        _button(buttons, "Close", self.close, width=96).pack(side="right")

        self.refresh_duty()
        self._render_results([])
        _center_on(top, m.window)
        top.after(200, self.query.focus_set)

    # ---- searching ----

    def _typed(self, event):
        if event.keysym in ("Return", "KP_Enter", "Escape"):
            return
        if self._search_job:
            self.top.after_cancel(self._search_job)
        self._search_job = self.top.after(self.SEARCH_PAUSE_MS, self._search_now)

    def _enter(self, _event=None):
        card = self._burst.take_card()
        if card:
            # A card tapped while the guard was typing here - it's a normal
            # gate tap, not a search.
            self.m._last_card_id = card
            if self.m.on_tap:
                self.m.on_tap(card)
            return "break"
        self._search_now()
        return "break"

    def _search_now(self):
        if self._search_job:
            self.top.after_cancel(self._search_job)
            self._search_job = None
        text = self.query.get().strip()
        if len(text) < 2:
            self.status.configure(text="Type at least 2 letters or digits.", text_color=INK_600)
            return
        self._asked += 1
        asked = self._asked
        self.status.configure(text="Searching…", text_color=INK_600)
        self._search(text, lambda answer: self._answered(asked, answer))

    def _answered(self, asked, answer):
        if asked != self._asked or not self.top.winfo_exists():
            return  # an older search, or the window closed meanwhile
        if answer.get("error"):
            self.status.configure(text=answer["error"], text_color=DANGER)
            return
        results = answer.get("results") or []
        self.status.configure(text=f"{len(results)} found - click one." if results else "Nobody found.",
                              text_color=INK_600)
        self._render_results(results)

    def _render_results(self, results):
        for widget in self.list.winfo_children():
            widget.destroy()
        self._results, self._rows, self._images = results, [], []
        self._chosen = None
        if not results:
            self.m._label(self.list, "Results appear here.", (FONT, 14), INK_400).pack(pady=24)
        for index, person in enumerate(results):
            row = ctk.CTkFrame(self.list, fg_color=SURFACE, corner_radius=6, border_width=2, border_color=SURFACE,
                               cursor="hand2")
            row.pack(fill="x", padx=4, pady=3)
            image = _photo(person.get("photo_bytes"), person.get("person_name"), self.PHOTO)
            self._images.append(image)
            photo = ctk.CTkLabel(row, text="", image=image)
            photo.pack(side="left", padx=8, pady=8)
            text = ctk.CTkFrame(row, fg_color="transparent")
            text.pack(side="left", fill="x", expand=True, pady=6)
            self.m._label(text, person.get("person_name") or "—", (FONT, 15, "bold"), INK, anchor="w").pack(fill="x")
            line = " · ".join(part for part in (person.get("student_or_employee_id"),
                                                person.get("department_or_course")) if part)
            self.m._label(text, line, (FONT_MONO, 12), INK_600, anchor="w", justify="left",
                          wraplength=WIDTH - 140).pack(fill="x")
            tags = []
            if person.get("is_active") is False:
                tags.append(("DEACTIVATED - can't be let in", DANGER))
            if person.get("not_enrolled"):
                tags.append(("NOT ENROLLED this semester", CAUTION))
            if person.get("distinguishing_note"):
                tags.append((f"Note: {person['distinguishing_note']}", PROMPT))
            for tag, color in tags:
                self.m._label(text, tag, (FONT, 12, "bold"), color, anchor="w").pack(fill="x")
            for widget in (row, photo, text, *text.winfo_children()):
                widget.bind("<Button-1>", lambda _e, i=index: self._choose(i))
            self._rows.append(row)
        if len(results) == 1:
            self._choose(0)
        self._refresh_button()

    def _choose(self, index):
        self._chosen = index
        for i, row in enumerate(self._rows):
            row.configure(border_color=MAROON if i == index else SURFACE)
        self._refresh_button()

    # ---- letting in ----

    def _reason_changed(self):
        if self.reason.get() == "Other":
            self.other.pack(fill="x", pady=(6, 0), before=self._direction_row)
            self.other.focus_set()
        else:
            self.other.pack_forget()

    def _on_duty(self):
        status = self.m._sign_in or {}
        return (status.get("on_duty") or {}).get("name") if status.get("enabled") else None

    def refresh_duty(self):
        """The line above Let in - who it's recorded under. The gate monitor
        calls it again when someone signs in or out."""
        if not self.top.winfo_exists():
            return
        status = self.m._sign_in or {}
        name = self._on_duty()
        if name:
            self.duty.configure(text=f"Recorded under {name} (on duty), with the reason.", text_color=INK_600)
        elif status.get("enabled"):
            self.duty.configure(text="Sign in for duty first - letting someone in by hand is recorded under the "
                                     "guard on duty.", text_color=CAUTION)
        else:
            self.duty.configure(text="Letting someone in by hand needs \"Guards sign in at the gate monitor\" "
                                     "switched on (dashboard Settings) - it's recorded under the guard on duty.",
                                text_color=CAUTION)
        self._refresh_button()

    def _refresh_button(self):
        person = self._results[self._chosen] if self._chosen is not None else None
        ready = (person is not None and person.get("is_active") is not False and self._on_duty()
                 and not self._busy)
        verb = "Let out" if self.direction.get() == "Exit" else "Let in"
        self.let_in_button.configure(state="normal" if ready else "disabled",
                                     text="Saving…" if self._busy else verb)

    def _let_in_now(self):
        if self._chosen is None or self._busy:
            return
        reason = self.reason.get()
        if reason == "Other":
            reason = self.other.get().strip()
            if not reason:
                self.status.configure(text="Type what was checked, or pick a reason.", text_color=DANGER)
                return
        person = self._results[self._chosen]
        direction = "exit" if self.direction.get() == "Exit" else "entry"
        self._busy = True
        self._refresh_button()
        self._let_in(person, direction, reason[:255], self._let_in_answer)

    def _let_in_answer(self, answer):
        self._busy = False
        if not self.top.winfo_exists():
            return
        if answer.get("ok"):
            self.close()  # the live log shows the entry (ui._render_card_match)
            return
        self.status.configure(text=answer.get("error") or "Couldn't save - try again.", text_color=DANGER)
        self._refresh_button()

    def lift(self):
        self.top.lift()
        self.query.focus_set()

    def close(self):
        if self._search_job:
            self.top.after_cancel(self._search_job)
            self._search_job = None
        if self.top in self.m._typing_windows:
            self.m._typing_windows.remove(self.top)
        self.top.destroy()
        self._on_close()


# ---- Quick guide ---------------------------------------------------------------

GUIDE_LOG = (
    ("entry", "Recognized - let in."),
    ("exit", "Recognized at the exit camera - leaving."),
    ("card", "Card accepted. With BY HAND: let in with Find person - recorded under the guard on duty."),
    ("unknown", "Face not recognized - check this person and ask for their ID. \"SAME UNKNOWN PERSON AGAIN\" "
                "means they keep coming back."),
    ("spoof", "A photo or screen held to the camera - not a live face. Stop the person."),
    ("covered", "Mouth or nose covered - ask them to uncover. Not logged, no alarm."),
    ("card_rejected", "Card not registered, or the person was deactivated."),
    ("card_offline", "No connection to the server - the tap is saved and sent later."),
    ("not_enrolled", "A student not on this semester's enrolled list - let in, flagged. In red with REFUSED "
                     "when Settings says to refuse them (let them in by hand if they should pass)."),
    ("after_hours", "Outside the gate's opening hours."),
    ("pattern", "NO ENTRY TODAY or NO EXIT YET - a scan was probably missed. Ask them."),
)

GUIDE_SOUNDS = (
    ("spoof", "Rising siren, three times", "Fake face suspected - the most urgent."),
    ("unknown", "Two bell notes, high-low, twice", "Unknown person."),
    ("not_enrolled", "Falling \"uh-oh\" buzz", "Student refused - not enrolled this semester."),
    ("covered", "Two soft notes, once per person", "Please uncover your face."),
)

GUIDE_BUTTONS = (
    ("Cameras & card readers", "Choose the entrance and exit cameras, and which card reader is the exit's."),
    ("Student display", "Opens the screen that faces the students."),
    ("Layout", "How the cameras and the live log are arranged - 7 choices, remembered on this computer."),
    ("Find person", "Look someone up by ID number or name; let them in or out by hand, with a reason."),
    ("On duty / Sign in", "Who is on duty here. Click it to sign in or out."),
    ("Speaker", f"Alert sound volume, or mute for {MUTE_MINUTES} minutes - they come back by themselves."),
    ("?", "This guide."),
)


class GuideWindow:
    def __init__(self, monitor, on_close):
        self.m = m = monitor
        self._on_close = on_close
        top = ctk.CTkToplevel(m.window)
        top.title("Quick guide")
        top.configure(fg_color=SURFACE)
        top.transient(m.window)
        top.protocol("WM_DELETE_WINDOW", self.close)
        top.bind("<Escape>", lambda _e: self.close())
        self.top = top
        head = ctk.CTkFrame(top, fg_color="transparent")
        head.pack(fill="x", padx=28, pady=(22, 6))
        m._label(head, "Quick guide", (SEMI_BOLD, 22), INK, anchor="w").pack(side="left")
        _button(head, "Close", self.close, width=90).pack(side="right")
        self.body = ctk.CTkScrollableFrame(top, fg_color="transparent", width=680, height=560)
        self.body.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        self._section("THE LIVE LOG AND THE CAMERA BOXES")
        for kind, text in GUIDE_LOG:
            spec = LOG_KINDS[kind]
            tint = spec.get("tint") or CANVAS
            row = self._row()
            chip = ctk.CTkFrame(row, fg_color=tint, corner_radius=4, width=150, height=28)
            chip.pack(side="left", anchor="n", padx=(0, 12))
            chip.pack_propagate(False)
            m._label(chip, spec["word"], (COND_BOLD, 13), spec["color"], fg_color=tint).pack(expand=True)
            m._label(row, text, (FONT, 14), INK, anchor="w", justify="left", wraplength=480).pack(
                side="left", fill="x", expand=True)
        self._note("Green: let in. Amber: check this person. Red: stop - a fake face or a refusal. Blue: a prompt "
                   "for the person, nothing wrong.")

        self._section("SOUNDS")
        for kind, sound, meaning in GUIDE_SOUNDS:
            row = self._row()
            play = f"{_icon('play')}  Play" if _icon("play") else "Play"
            ctk.CTkButton(row, text=play, width=86, height=30, corner_radius=6, font=(FONT, 13, "bold"),
                          fg_color=SURFACE, hover_color=CANVAS, text_color=INK, border_width=1, border_color=INK_400,
                          command=lambda k=kind: m._sounds.play(k, preview=True)).pack(side="left", padx=(0, 12))
            text = ctk.CTkFrame(row, fg_color="transparent")
            text.pack(side="left", fill="x", expand=True)
            m._label(text, sound, (FONT, 14, "bold"), INK, anchor="w").pack(fill="x")
            m._label(text, meaning, (FONT, 13), INK_600, anchor="w").pack(fill="x")
        self._note("Each sound can be switched off on the dashboard (Settings > Alerts). The speaker button at the "
                   "bottom sets the volume on this computer.")

        self._section("THE CARD PILL ON THE CAMERA")
        self._note("\"Tap a card\" waits for a card. After a tap it shows what happened for 8 seconds. A guard's own "
                   "staff ID card signs them in for duty instead - it's never logged as an entry.")

        self._section("BUTTONS")
        for name, text in GUIDE_BUTTONS:
            row = self._row()
            m._label(row, name, (FONT, 14, "bold"), INK, anchor="nw", justify="left", width=190,
                     wraplength=180).pack(side="left", anchor="n")
            m._label(row, text, (FONT, 14), INK_600, anchor="w", justify="left", wraplength=450).pack(
                side="left", fill="x", expand=True)

        self._section("CLOSING")
        self._note("Escape or the window's X asks first. Closing stops the gate's scanning and signs out the guard "
                   "on duty.")
        _center_on(top, m.window)

    def _section(self, title):
        tk.Frame(self.body, bg=LINE, height=1).pack(fill="x", padx=12, pady=(14, 8))
        self.m._label(self.body, title, (COND_BOLD, 14), MAROON, anchor="w").pack(fill="x", padx=12, pady=(0, 6))

    def _row(self):
        row = ctk.CTkFrame(self.body, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=4)
        return row

    def _note(self, text):
        self.m._label(self.body, text, (FONT, 14), INK_600, anchor="w", justify="left", wraplength=640).pack(
            fill="x", padx=12, pady=(6, 2))

    def lift(self):
        self.top.lift()

    def close(self):
        self.top.destroy()
        self._on_close()


# ---- Sound menu ----------------------------------------------------------------


class SoundMenu:
    """Pops up above the speaker button in the status bar. on_change(volume) is
    main.py's - it remembers the volume on this computer."""

    def __init__(self, monitor, anchor, on_change, on_close):
        self.m = m = monitor
        self._on_change = on_change
        self._on_close = on_close
        self._save_job = None
        sounds = m._sounds
        menu = ctk.CTkFrame(m.window, fg_color=SURFACE, corner_radius=10, border_width=1, border_color=INK_400)
        self.frame = menu
        m._label(menu, "ALERT SOUNDS", (COND_BOLD, 12), INK_600, anchor="w").pack(fill="x", padx=18, pady=(12, 6))
        row = ctk.CTkFrame(menu, fg_color="transparent")
        row.pack(fill="x", padx=14)
        m._icon_label(row, "speaker-none", 18, INK_600).pack(side="left")
        self.slider = ctk.CTkSlider(row, from_=0, to=100, number_of_steps=20, width=200, button_color=MAROON,
                                    button_hover_color=MAROON_DEEP, progress_color=MAROON, fg_color=LINE,
                                    command=self._slid)
        self.slider.set(round(sounds.volume * 100))
        self.slider.pack(side="left", padx=8)
        m._icon_label(row, "speaker-high", 18, INK_600).pack(side="left")
        self.percent = m._label(row, "", (FONT_MONO, 14), INK, width=48)
        self.percent.pack(side="left", padx=(8, 0))
        buttons = ctk.CTkFrame(menu, fg_color="transparent")
        buttons.pack(fill="x", padx=14, pady=(10, 14))
        _button(buttons, "Test", lambda: sounds.play("unknown", preview=True), width=70, height=32).pack(side="left")
        self.mute_button = _button(buttons, "", self._toggle_mute, primary=True, width=200, height=32)
        self.mute_button.pack(side="left", padx=(10, 0))
        self._render()
        # Pops up above its button in the status bar, like the Layout menu.
        m.window.update_idletasks()
        x = anchor.winfo_rootx() - m.window.winfo_rootx()
        y = anchor.winfo_rooty() - m.window.winfo_rooty() - m._px(10)
        menu.place(x=x, y=y, anchor="sw")
        menu.lift()

    def _render(self):
        sounds = self.m._sounds
        self.percent.configure(text=f"{round(sounds.volume * 100)}%")
        left = sounds.muted_seconds_left()
        self.mute_button.configure(text=f"Unmute ({max(1, round(left / 60))} min left)" if left
                                   else f"Mute for {MUTE_MINUTES} minutes")

    def _slid(self, value):
        self.m._sounds.set_volume(value / 100)
        self._render()
        self.m._refresh_sound_button()
        if self._save_job:
            self.frame.after_cancel(self._save_job)
        self._save_job = self.frame.after(400, self._save)

    def _save(self):
        self._save_job = None
        if self._on_change:
            self._on_change(self.m._sounds.volume)

    def _toggle_mute(self):
        sounds = self.m._sounds
        if sounds.muted_seconds_left():
            sounds.unmute()
        else:
            sounds.mute()
        self._render()
        self.m._refresh_sound_button()

    def owns(self, widget_name):
        return widget_name.startswith(str(self.frame))

    def close(self):
        if self._save_job:
            self.frame.after_cancel(self._save_job)
            self._save()
        self.frame.destroy()
        self._on_close()


# ---- "Use it as the exit camera?" ----------------------------------------------


class ExitCameraQuestion:
    """answer("use" | "later" | "never") - main.py's."""

    def __init__(self, monitor, camera_name, answer):
        self.m = m = monitor
        self._answer = answer
        top = ctk.CTkToplevel(m.window)
        top.title("New camera")
        top.configure(fg_color=SURFACE)
        top.resizable(False, False)
        top.transient(m.window)
        top.protocol("WM_DELETE_WINDOW", lambda: self._done("later"))
        self.top = top
        body = ctk.CTkFrame(top, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=28, pady=22)
        head = ctk.CTkFrame(body, fg_color="transparent")
        head.pack(fill="x")
        m._icon_label(head, "video-camera", 26, PROMPT).pack(side="left", padx=(0, 10))
        m._label(head, "A second camera is plugged in", (SEMI_BOLD, 20), INK, anchor="w").pack(side="left")
        m._label(body, camera_name, (FONT_MONO, 14), INK, anchor="w").pack(fill="x", pady=(10, 4))
        m._label(body, "Use it as this gate's EXIT camera? The gate then records people leaving too - point it at "
                       "the way out. You can change it any time in Cameras & card readers.",
                 (FONT, 14), INK_600, anchor="w", justify="left", wraplength=460).pack(fill="x")
        buttons = ctk.CTkFrame(body, fg_color="transparent")
        buttons.pack(fill="x", pady=(18, 0))
        _button(buttons, "Use as exit camera", lambda: self._done("use"), primary=True, width=170).pack(side="left")
        _button(buttons, "Not now", lambda: self._done("later"), width=90).pack(side="left", padx=(10, 0))
        _button(buttons, "Don't ask again", lambda: self._done("never"), width=130, border_width=0,
                text_color=INK_600).pack(side="right")
        _center_on(top, m.window)

    def _done(self, choice):
        if self.top.winfo_exists():
            self.top.destroy()
        self._answer(choice)
