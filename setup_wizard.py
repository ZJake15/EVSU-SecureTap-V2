"""EVSU SecureTap - first-run setup.

Shown by the launcher the first time it opens on a computer (and again from
its "Run setup again" button). Five steps, each skipped over quickly when it's
already done:

  1. Welcome - creates backend/.env and entry-agent/.env on a new computer.
  2. Your data - start empty, keep what's here, or bring the data over from
     an older SecureTap copy (backend: manage.py import_securetap).
  3. Admin account - the first one, typed in here; never a default password.
  4. Camera and card reader - a quick check, never a blocker.
  5. Speed test - measures this computer and picks Fast / Standard / Light
     (device_setup.py); the choice can be changed here or in the launcher.

Every long step runs off the Tk thread, so the window never freezes.
"""

import json
import queue
import shutil
import subprocess
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog

import customtkinter as ctk
from PIL import Image

import device_setup
from ui import (
    BRASS,
    CANVAS,
    CAUTION,
    DANGER,
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
)

BACKEND_DIR = device_setup.BACKEND_DIR
_NO_WINDOW = device_setup._NO_WINDOW

STEPS = ("Welcome", "Your data", "Admin account", "Camera and card reader", "Speed test")
CONTENT_WIDTH = 560
# InsightFace and Python print these while the face models load - noise in
# the setup window's progress log.
_NOISE = ("Applied providers", "model ignore", "find model", "set det-size", "FutureWarning", "tform.estimate")
RESULT_MARKER = "SECURETAP_RESULT"
# A backup made with the launcher's "Back up data" (backend: manage.py
# backup_data) - backend/configuration/backup_file.py's SUFFIX.
BACKUP_SUFFIX = ".securetap-backup"


def _icon_label(parent, name, size, color, bold=True, **kwargs):
    family = ICON_FONT_BOLD if bold else ICON_FONT
    return ctk.CTkLabel(parent, text=_icon(name, bold), font=(family or FONT, size), text_color=color, **kwargs)


def _is_securetap_folder(path):
    return (Path(path) / "backend" / "manage.py").exists()


def find_old_copies():
    """Other SecureTap folders on this computer worth offering for import:
    next to this one, or one or two levels inside Documents, Desktop and
    Downloads. Only folders with a backend/.env (a copy that was actually
    set up)."""
    here = device_setup.ROOT.resolve()
    home = Path.home()
    places = [here.parent] + [home / name for name in ("Documents", "Desktop", "Downloads")]
    found = []
    for place in places:
        try:
            candidates = [place] + [child for child in place.iterdir() if child.is_dir()]
            candidates += [grandchild for child in candidates[1:] for grandchild in child.iterdir()
                           if grandchild.is_dir()]
        except OSError:
            continue
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
                if (resolved != here and resolved not in found and _is_securetap_folder(resolved)
                        and (resolved / "backend" / ".env").exists()):
                    found.append(resolved)
            except OSError:
                continue
    return found


class SetupWizard:
    def __init__(self, python, backend_running, reader_check):
        """python: the venv interpreter. backend_running(): whether a backend
        already answers on port 8000. reader_check(): True/False/None for "is
        a card reader plugged in" (the launcher's own check)."""
        self.python = python
        self._backend_running = backend_running
        self._reader_check = reader_check
        self.completed = False
        self._busy = False
        self.status = {}
        self.imported_from = None
        self._measured = None
        self._recommended = None
        self._reason = None
        self._lines = queue.Queue()

        self.root = ctk.CTk()
        self.root.title("EVSU SecureTap - Setup")
        self.root.configure(fg_color=CANVAS)
        fit_to_screen(self.root, 640, 760, 560, 560)
        self.root.protocol("WM_DELETE_WINDOW", self._quit)
        _apply_icon(self.root)

        self._build_header()
        self._build_footer()
        self.body = ctk.CTkScrollableFrame(self.root, fg_color=CANVAS, corner_radius=0,
                                           scrollbar_button_color=LINE, scrollbar_button_hover_color=INK_400)
        self.body.pack(fill="both", expand=True)
        self.content = ctk.CTkFrame(self.body, fg_color="transparent")
        self.content.pack(fill="x", padx=26, pady=(18, 18))
        self._show_welcome()

    def run(self):
        self.root.mainloop()
        return self.completed

    # ---- frame --------------------------------------------------------------

    def _build_header(self):
        header = ctk.CTkFrame(self.root, fg_color=MAROON_DEEP, corner_radius=0, height=76)
        header.pack(fill="x")
        header.pack_propagate(False)
        ctk.CTkFrame(self.root, fg_color=BRASS, corner_radius=0, height=3).pack(fill="x")
        inner = ctk.CTkFrame(header, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=26)
        try:
            seal = Image.open(ICON_PATH).convert("RGBA")
            self._seal = ctk.CTkImage(light_image=seal, dark_image=seal, size=(48, 48))
            ctk.CTkLabel(inner, image=self._seal, text="").pack(side="left", padx=(0, 20))
        except Exception:
            pass  # a missing seal asset shouldn't stop setup
        text = ctk.CTkFrame(inner, fg_color="transparent")
        text.pack(side="left")
        wordmark = ctk.CTkFrame(text, fg_color="transparent")
        wordmark.pack(anchor="w")
        ctk.CTkLabel(wordmark, text="EVSU", font=(WIDE_BLACK, 28), text_color="white", height=30).pack(
            side="left", anchor="s", padx=(0, 10))
        ctk.CTkLabel(wordmark, text="SecureTap", font=(SEMI_HEAVY, 28), text_color="white", height=30).pack(
            side="left", anchor="s")
        ctk.CTkLabel(text, text="First-time setup", font=(FONT, 16), text_color="white", height=22).pack(
            anchor="w", pady=(4, 0))

    def _build_footer(self):
        footer = ctk.CTkFrame(self.root, fg_color=SURFACE, corner_radius=0)
        footer.pack(side="bottom", fill="x")
        tk.Frame(self.root, bg=LINE, height=1).pack(side="bottom", fill="x")
        inner = ctk.CTkFrame(footer, fg_color="transparent")
        inner.pack(fill="x", padx=26, pady=10)
        self.step_label = ctk.CTkLabel(inner, text="", font=(FONT, 13), text_color=INK_600)
        self.step_label.pack(side="left")
        self.primary = ctk.CTkButton(
            inner, text="Start", font=(FONT, 14, "bold"), height=44, corner_radius=8, fg_color=MAROON,
            hover_color=MAROON_DEEP, text_color="white", text_color_disabled="#E9D9DC",
        )
        self.primary.pack(side="right")
        self.secondary = ctk.CTkButton(
            inner, text="", font=(FONT, 14, "bold"), height=44, corner_radius=8, fg_color=SURFACE,
            hover_color=CANVAS, text_color=INK, border_width=1, border_color=LINE,
        )

    def _step(self, index, title, primary_text, primary_command):
        """Clears the page for step `index` (0-based) and sets its title and
        main button."""
        for child in self.content.winfo_children():
            child.destroy()
        self.step_label.configure(text=f"Step {index + 1} of {len(STEPS)} · {STEPS[index]}")
        ctk.CTkLabel(self.content, text=title, font=(SEMI_HEAVY, 22), text_color=INK, anchor="w").pack(fill="x")
        self._set_primary(primary_text, primary_command)
        self._set_secondary(None)
        self.body._parent_canvas.yview_moveto(0)

    def _set_primary(self, text, command, enabled=True):
        self.primary.configure(text=text, command=command, state="normal" if enabled else "disabled")

    def _set_secondary(self, text, command=None):
        if text is None:
            self.secondary.pack_forget()
            return
        self.secondary.configure(text=text, command=command)
        self.secondary.pack(side="right", padx=(0, 10))

    def _text(self, parent, text, color=INK, size=14, bold=False, pady=(10, 0)):
        label = ctk.CTkLabel(parent, text=text, font=(FONT, size, "bold") if bold else (FONT, size),
                             text_color=color, anchor="w", justify="left", wraplength=CONTENT_WIDTH)
        label.pack(fill="x", pady=pady)
        return label

    def _card(self, pady=(14, 0)):
        card = ctk.CTkFrame(self.content, fg_color=SURFACE, corner_radius=8, border_width=1, border_color=LINE)
        card.pack(fill="x", pady=pady)
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=14)
        return inner

    def _status_row(self, parent, ok, title, detail=None):
        """ok: True (green check), False (amber warning) or None (grey)."""
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(0, 10))
        icon, color = {True: ("check-circle", VERIFIED), False: ("warning", CAUTION)}.get(ok, ("circle", INK_600))
        _icon_label(row, icon, 20, color).pack(side="left", anchor="n", padx=(0, 10))
        text = ctk.CTkFrame(row, fg_color="transparent")
        text.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(text, text=title, font=(FONT, 14, "bold"), text_color=INK, anchor="w", justify="left",
                     wraplength=CONTENT_WIDTH - 60).pack(fill="x")
        if detail:
            ctk.CTkLabel(text, text=detail, font=(FONT, 13), text_color=INK_600, anchor="w", justify="left",
                         wraplength=CONTENT_WIDTH - 60).pack(fill="x")
        return row

    def _choices(self, options, selected, on_change=None):
        """Big clickable option cards: [(value, title, detail)]. Returns a
        tk.StringVar holding the chosen value."""
        var = tk.StringVar(value=selected)
        cards = {}

        def paint():
            for value, (card, icon) in cards.items():
                chosen = var.get() == value
                card.configure(border_color=MAROON if chosen else LINE, border_width=2 if chosen else 1)
                icon.configure(text=_icon("check-circle" if chosen else "circle"),
                               text_color=MAROON if chosen else INK_400)

        def choose(value):
            var.set(value)
            paint()
            if on_change:
                on_change(value)

        for value, title, detail in options:
            card = ctk.CTkFrame(self.content, fg_color=SURFACE, corner_radius=8, border_width=1,
                                border_color=LINE, cursor="hand2")
            card.pack(fill="x", pady=(12, 0))
            inner = ctk.CTkFrame(card, fg_color="transparent", cursor="hand2")
            inner.pack(fill="x", padx=16, pady=12)
            icon = _icon_label(inner, "circle", 20, INK_400, cursor="hand2")
            icon.pack(side="left", anchor="n", padx=(0, 12))
            text = ctk.CTkFrame(inner, fg_color="transparent", cursor="hand2")
            text.pack(side="left", fill="x", expand=True)
            widgets = [card, inner, icon, text]
            widgets.append(ctk.CTkLabel(text, text=title, font=(FONT, 15, "bold"), text_color=INK, anchor="w",
                                        justify="left", cursor="hand2", wraplength=CONTENT_WIDTH - 70))
            widgets[-1].pack(fill="x")
            if detail:
                widgets.append(ctk.CTkLabel(text, text=detail, font=(FONT, 13), text_color=INK_600, anchor="w",
                                            justify="left", cursor="hand2", wraplength=CONTENT_WIDTH - 70))
                widgets[-1].pack(fill="x", pady=(2, 0))
            for widget in widgets:
                widget.bind("<Button-1>", lambda _event, v=value: choose(v))
            cards[value] = (card, icon)
        paint()
        return var

    def _progress(self, message):
        """A "working on it" block: message, moving bar and a small log box.
        Returns (message label, log box)."""
        holder = self._card()
        label = ctk.CTkLabel(holder, text=message, font=(FONT, 14, "bold"), text_color=PROMPT, anchor="w",
                             justify="left", wraplength=CONTENT_WIDTH - 40)
        label.pack(fill="x")
        bar = ctk.CTkProgressBar(holder, height=4, corner_radius=2, mode="indeterminate", fg_color=LINE,
                                 progress_color=PROMPT)
        bar.pack(fill="x", pady=(8, 0))
        bar.start()
        log = ctk.CTkTextbox(holder, font=(FONT_MONO, 11), fg_color=CANVAS, text_color=INK_600, corner_radius=3,
                             border_width=0, wrap="word", height=150)
        log.pack(fill="x", pady=(10, 0))
        log.configure(state="disabled")
        label.bar = bar
        # It's added under the page's choices - scroll down to it, or a short
        # window hides the progress (and any error) below the fold.
        self.root.after(100, lambda: self.body._parent_canvas.yview_moveto(1.0))
        return label, log

    @staticmethod
    def _log(box, line):
        box.configure(state="normal")
        box.insert("end", line + "\n")
        box.see("end")
        box.configure(state="disabled")

    # ---- running things off the Tk thread ------------------------------------

    def _background(self, work, done):
        """work() on a thread, then done(result, error) on the Tk thread."""
        box = {}

        def runner():
            try:
                box["result"] = work()
            except Exception as exc:  # reported in the window, never a crash
                box["error"] = exc

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()

        def check():
            if thread.is_alive():
                self.root.after(80, check)
            else:
                done(box.get("result"), box.get("error"))

        self.root.after(80, check)

    def _manage(self, *args, stdin=None):
        """Runs backend/manage.py with args and waits: (exit code, output)."""
        result = subprocess.run(
            [self.python, "manage.py", *args], cwd=BACKEND_DIR, input=stdin, capture_output=True, text=True,
            encoding="utf-8", errors="replace", creationflags=_NO_WINDOW,
        )
        return result.returncode, (result.stdout + result.stderr).strip()

    def _stream(self, args, log_box, done, secret=None):
        """Runs backend/manage.py with args, showing its output in log_box as
        it comes, then done(exit code, all output lines) on the Tk thread.
        secret (a password) goes in as the first line of its input - never
        on the command line, where other programs could see it."""
        process = subprocess.Popen(
            [self.python, "-u", "manage.py", *args], cwd=BACKEND_DIR, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL if secret is None else subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace", creationflags=_NO_WINDOW,
        )
        if secret is not None:
            process.stdin.write(secret + "\n")
            process.stdin.close()
        lines = []

        def pump():
            for line in process.stdout:
                self._lines.put(line.rstrip())
            process.stdout.close()
            process.wait()

        thread = threading.Thread(target=pump, daemon=True)
        thread.start()

        def drain():
            while True:
                try:
                    line = self._lines.get_nowait()
                except queue.Empty:
                    break
                lines.append(line)
                if line and not any(noise in line for noise in _NOISE) and not line.startswith(RESULT_MARKER):
                    self._log(log_box, line)
            if thread.is_alive():
                self.root.after(100, drain)
            else:
                done(process.returncode, lines)

        self.root.after(100, drain)

    @staticmethod
    def _error_from(output):
        """The useful line of a failed command's output - Django prints
        "CommandError: <reason>" last."""
        lines = [line for line in output.splitlines() if line.strip()]
        for line in reversed(lines):
            if "Error:" in line:
                return line.split("Error:", 1)[1].strip()
        return lines[-1] if lines else "Something went wrong."

    def _refresh_status(self):
        code, output = self._manage("setup_status")
        if code != 0:
            raise RuntimeError(self._error_from(output))
        self.status = json.loads(output.splitlines()[-1])
        return self.status

    def _set_busy(self, busy):
        self._busy = busy
        self.primary.configure(state="disabled" if busy else "normal")
        self.secondary.configure(state="disabled" if busy else "normal")

    def _quit(self):
        if self._busy and not messagebox.askokcancel(
            "Quit setup", "Setup is still working on something. Quit anyway?\n\nYou can finish setup the next "
                          "time you open SecureTap.", parent=self.root):
            return
        if not self._busy and not self.completed and not messagebox.askokcancel(
            "Quit setup", "Quit setup? You can finish it the next time you open SecureTap.", parent=self.root):
            return
        self.root.destroy()

    # ---- 1. welcome -----------------------------------------------------------

    def _show_welcome(self):
        self._step(0, "Welcome", "Start", self._prepare)
        self._text(self.content, "Let's get SecureTap ready on this computer. It takes a few minutes, and anything "
                                 "that's already done is skipped.", INK_600)
        card = self._card()
        for number, name in enumerate(STEPS, start=1):
            if number == 1:
                self._text(card, f"{number}.  {name}  (you're here)", bold=True, pady=(0, 6))
            else:
                self._text(card, f"{number}.  {name}", pady=(0, 6))

    def _prepare(self):
        self._set_busy(True)
        label, log = self._progress("Preparing this copy...")

        def work():
            created = device_setup.create_env_files()
            return created, self._refresh_status()

        def done(result, error):
            self._set_busy(False)
            if error:
                label.bar.stop()
                label.configure(text=f"Couldn't prepare this copy: {error}", text_color=DANGER)
                self._set_primary("Try again", self._prepare)
                return
            self._show_data()

        self._background(work, done)

    # ---- 2. data ------------------------------------------------------------

    def _show_data(self):
        status = self.status
        has_data = any(status.get(key) for key in ("people", "entry_logs", "accounts"))
        self._step(1, "Your data", "Continue", self._apply_data_choice)
        self._old_copies = find_old_copies()
        self._folder = tk.StringVar(value=str(self._old_copies[0]) if self._old_copies else "")
        if has_data:
            self._text(self.content, f"This computer already has SecureTap data: {status['people']} people, "
                                     f"{status['entry_logs']} entry records and {status['accounts']} accounts.",
                       INK_600)
            options = [("keep", "Keep this data", "Nothing is changed."),
                       ("import", "Replace it with another copy's data, or a backup's",
                        "The data here is kept as a backup file, not deleted.")]
            selected = "keep"
        else:
            self._text(self.content, "Do you have an older SecureTap copy with people already enrolled, or a backup "
                                     "file? Bring its data over so nobody has to be registered again.", INK_600)
            options = [("import", "Bring the data from an older copy or a backup",
                        "People and their faces, entry records, accounts, settings, photos and the gate's own "
                        "settings come along. The old copy or backup isn't changed."),
                       ("empty", "Start empty", "No people yet - register them on the dashboard.")]
            selected = "import" if self._old_copies else "empty"
        self._data_choice = self._choices(options, selected, on_change=self._toggle_folder_picker)

        self._folder_card = ctk.CTkFrame(self.content, fg_color="transparent")
        ctk.CTkLabel(self._folder_card, text="The older copy's folder, or a backup file", font=(FONT, 14, "bold"),
                     text_color=INK, anchor="w").pack(fill="x")
        ctk.CTkEntry(self._folder_card, textvariable=self._folder, height=40, corner_radius=3, border_width=1,
                     border_color=LINE, fg_color=SURFACE, text_color=INK, font=(FONT, 13)).pack(fill="x", pady=(6, 0))
        row = ctk.CTkFrame(self._folder_card, fg_color="transparent")
        row.pack(fill="x", pady=(8, 0))
        for text, command in (("Choose folder...", self._pick_folder), ("Choose backup file...", self._pick_backup_file)):
            ctk.CTkButton(row, text=text, font=(FONT, 14, "bold"), height=40, corner_radius=8, fg_color=SURFACE,
                          hover_color=CANVAS, text_color=INK, border_width=1, border_color=LINE,
                          command=command).pack(side="left", padx=(0, 10))
        hint = ("Found on this computer: " + ", ".join(str(path) for path in self._old_copies[:3])
                if self._old_copies else "The folder that holds the old copy's backend, dashboard and entry-agent "
                                         "folders - or a backup file made with \"Back up data\" in the launcher.")
        ctk.CTkLabel(self._folder_card, text=hint, font=(FONT, 12), text_color=INK_600, anchor="w", justify="left",
                     wraplength=CONTENT_WIDTH).pack(fill="x", pady=(4, 0))
        self._toggle_folder_picker(selected)

    def _toggle_folder_picker(self, value):
        if value == "import":
            self._folder_card.pack(fill="x", pady=(14, 0))
        else:
            self._folder_card.pack_forget()

    def _pick_folder(self):
        chosen = filedialog.askdirectory(parent=self.root, title="Choose the older SecureTap copy's folder",
                                         initialdir=self._folder.get() or str(Path.home()))
        if chosen:
            self._folder.set(str(Path(chosen)))

    def _pick_backup_file(self):
        chosen = filedialog.askopenfilename(
            parent=self.root, title="Choose the SecureTap backup file",
            filetypes=[("SecureTap backup", "*" + BACKUP_SUFFIX), ("All files", "*.*")],
        )
        if chosen:
            self._folder.set(str(Path(chosen)))

    def _apply_data_choice(self):
        choice = self._data_choice.get()
        if choice in ("keep", "empty"):
            if self.status.get("database_ready"):
                self._show_admin()
                return
            self._run_migrate()
            return

        folder = self._folder.get().strip()
        is_backup = bool(folder) and Path(folder).is_file() and folder.lower().endswith(BACKUP_SUFFIX)
        if is_backup:
            if self._backend_running():
                messagebox.showerror(
                    "Close the other SecureTap first",
                    "A SecureTap backend is already running on this computer. Close it (and its launcher) first, so "
                    "the data can be brought in safely.", parent=self.root)
                return
            replacing = any(self.status.get(key) for key in ("people", "entry_logs", "accounts"))
            if replacing and not messagebox.askokcancel(
                "Replace this computer's data?",
                f"This replaces the data here ({self.status['people']} people, {self.status['entry_logs']} entry "
                "records) with the backup's. The current data is kept as a backup file (db.sqlite3.bak).",
                parent=self.root):
                return
            password = simpledialog.askstring(
                "Backup password", "The password this backup was locked with:", show="•", parent=self.root)
            if password:
                self._run_restore(folder, password, replacing)
            return
        if not folder or not _is_securetap_folder(folder):
            messagebox.showerror("Choose the older copy", "That folder isn't a SecureTap copy - choose the folder "
                                 "that holds its backend, dashboard and entry-agent folders.", parent=self.root)
            return
        if Path(folder).resolve() == device_setup.ROOT.resolve():
            messagebox.showerror("Choose the older copy", "That's this copy's own folder - choose the other one.",
                                 parent=self.root)
            return
        if self._backend_running():
            messagebox.showerror(
                "Close the other SecureTap first",
                "A SecureTap backend is already running on this computer. Close it (and its launcher) first, so the "
                "data can be copied safely.", parent=self.root)
            return
        replacing = any(self.status.get(key) for key in ("people", "entry_logs", "accounts"))
        if replacing and not messagebox.askokcancel(
            "Replace this computer's data?",
            f"This replaces the data here ({self.status['people']} people, {self.status['entry_logs']} entry "
            "records) with the older copy's. The current data is kept as a backup file (db.sqlite3.bak).",
            parent=self.root):
            return
        self._run_import(folder, replacing)

    def _run_migrate(self):
        self._set_busy(True)
        label, log = self._progress("Creating the database...")

        def done(code, lines):
            label.bar.stop()
            if code != 0:
                self._set_busy(False)
                label.configure(text="Couldn't create the database: " + self._error_from("\n".join(lines)),
                                text_color=DANGER)
                return

            def show(_result, _error):
                # Busy until the next page is up, so nothing can be clicked
                # in between.
                self._set_busy(False)
                self._show_admin()

            self._background(self._refresh_status, show)

        self._stream(["migrate"], log, done)

    def _run_restore(self, backup, password, replacing):
        """Unlocks a backup file into a temporary folder (manage.py
        open_backup), then brings it in like an old copy. The unlocked folder
        holds the photos and database readable, so it's deleted again
        whatever happens."""
        self._set_busy(True)
        label, log = self._progress("Unlocking the backup...")
        unlocked = tempfile.mkdtemp(prefix="securetap_restore_")

        def done(code, lines):
            label.bar.stop()
            if code != 0:
                shutil.rmtree(unlocked, ignore_errors=True)
                self._set_busy(False)
                label.configure(text="Couldn't open the backup: " + self._error_from("\n".join(lines)),
                                text_color=DANGER)
                self._set_primary("Try again", self._apply_data_choice)
                return
            label.configure(text="The backup is unlocked.", text_color=VERIFIED)
            self._run_import(unlocked, replacing, source=backup, cleanup=unlocked)

        self._stream(["open_backup", backup, unlocked], log, done, secret=password)

    def _run_import(self, folder, replacing, source=None, cleanup=None):
        """Brings the data in from folder. source: what to call it on the
        result page (the backup file, for a restore); cleanup: a temporary
        folder to delete once the import is done."""
        self._set_busy(True)
        label, log = self._progress("Bringing the data over... (a minute or two for a lot of photos)")
        args = ["import_securetap", folder] + (["--replace"] if replacing else [])

        def done(code, lines):
            label.bar.stop()
            if code != 0:
                if cleanup:
                    shutil.rmtree(cleanup, ignore_errors=True)
                self._set_busy(False)
                label.configure(text="Couldn't bring the data over: " + self._error_from("\n".join(lines)),
                                text_color=DANGER)
                self._set_primary("Try again", self._apply_data_choice)
                return
            result = next((json.loads(line.split(" ", 1)[1]) for line in reversed(lines)
                           if line.startswith(RESULT_MARKER)), {})
            try:
                brought = device_setup.import_gate_settings(folder)
            except OSError as exc:
                brought = []
                self._log(log, f"Couldn't bring the gate settings over: {exc}")
            if cleanup:
                shutil.rmtree(cleanup, ignore_errors=True)
            self.imported_from = source or folder
            if result.get("faces_need_recompute"):
                label.configure(text="Updating the face data for this copy's face model...")
                label.bar.start()
                self._stream(["recompute_embeddings"], log,
                             lambda _code, _lines: self._finish_import(label, result, brought))
                return
            self._finish_import(label, result, brought)

        self._stream(args, log, done)

    def _finish_import(self, label, result, brought):
        label.bar.stop()
        label.configure(text="Done - the data is here.", text_color=VERIFIED)

        def show(_result, _error):
            # A clean page for the result - the choices above it are done with.
            self._set_busy(False)
            self._step(1, "Your data", "Continue", self._show_admin)
            unchanged = ("The backup file wasn't changed." if str(self.imported_from).lower().endswith(BACKUP_SUFFIX)
                         else "The old copy wasn't changed.")
            self._text(self.content, f"The data from {self.imported_from} is here. {unchanged}", INK_600)
            card = self._card()
            self._status_row(card, True, f"{result.get('people', 0)} people with {result.get('faces', 0)} face "
                                         f"photos' data, {result.get('entry_logs', 0)} entry records and "
                                         f"{result.get('accounts', 0)} accounts")
            if result.get("faces_checked"):
                self._status_row(card, True, "Faces still match their photos",
                                 f"{result['faces_checked']} checked")
            if result.get("photos_copied"):
                self._status_row(card, True, f"{result['photos_copied']} photo files copied")
            if result.get("model_copied"):
                self._status_row(card, True, "The trained covered-face model")
            for item in brought:
                self._status_row(card, True, item[0].upper() + item[1:])

        self._background(self._refresh_status, show)

    # ---- 3. admin ---------------------------------------------------------------

    def _show_admin(self):
        admins = self.status.get("active_admins", 0)
        if admins:
            self._step(2, "Admin account", "Continue", self._show_devices)
            card = self._card()
            detail = ("They came from the older copy - same usernames and passwords." if self.imported_from
                      else "Sign in to the dashboard with one of them.")
            self._status_row(card, True, f"This copy has {admins} Admin account{'s' if admins != 1 else ''}", detail)
            return

        self._step(2, "Admin account", "Create account", self._create_admin)
        self._text(self.content, "Create the first Admin account. You'll use it to sign in to the dashboard, where "
                                 "you can add the other accounts.", INK_600)
        card = self._card()
        self._admin_fields = {}
        for key, label, secret in (("first", "First name", False), ("last", "Last name", False),
                                   ("username", "Username", False), ("password", "Password", True),
                                   ("confirm", "Type the password again", True)):
            ctk.CTkLabel(card, text=label, font=(FONT, 14, "bold"), text_color=INK, anchor="w").pack(fill="x")
            entry = ctk.CTkEntry(card, height=40, corner_radius=3, border_width=1, border_color=LINE,
                                 fg_color=SURFACE, text_color=INK, font=(FONT, 14), show="•" if secret else "")
            entry.pack(fill="x", pady=(4, 12))
            self._admin_fields[key] = entry
        ctk.CTkLabel(card, text="At least 10 characters - not only numbers, and not a common password.",
                     font=(FONT, 12), text_color=INK_600, anchor="w").pack(fill="x")
        self._admin_error = ctk.CTkLabel(card, text="", font=(FONT, 13, "bold"), text_color=DANGER, anchor="w",
                                         justify="left", wraplength=CONTENT_WIDTH - 40)
        self._admin_error.pack(fill="x", pady=(8, 0))
        self._admin_fields["first"].focus_set()

    def _create_admin(self):
        values = {key: entry.get() for key, entry in self._admin_fields.items()}
        if not values["username"].strip():
            self._admin_error.configure(text="Enter a username.")
            return
        if values["password"] != values["confirm"]:
            self._admin_error.configure(text="The two passwords don't match.")
            return
        self._admin_error.configure(text="")
        self._set_busy(True)

        def work():
            return self._manage("create_first_admin", "--username", values["username"].strip(),
                                "--first-name", values["first"].strip(), "--last-name", values["last"].strip(),
                                stdin=values["password"] + "\n")

        def done(result, error):
            self._set_busy(False)
            code, output = result if result else (1, str(error))
            if code != 0:
                self._admin_error.configure(text=self._error_from(output))
                return
            for key in ("password", "confirm"):
                self._admin_fields[key].delete(0, "end")
            self.status["active_admins"] = 1
            self._show_devices()

        self._background(work, done)

    # ---- 4. devices -------------------------------------------------------------

    def _show_devices(self):
        self._step(3, "Camera and card reader", "Continue", self._show_speed)
        self._text(self.content, "A quick check that the gate's hardware is plugged in. Nothing here stops you - "
                                 "you can plug things in later.", INK_600)
        self._device_card = self._card()
        self._text(self._device_card, "Checking...", PROMPT, bold=True, pady=(0, 0))
        self._set_busy(True)

        def work():
            return (device_setup.list_cameras(self.python), self._reader_check(), device_setup.gate_key_matches())

        def done(result, error):
            self._set_busy(False)
            self._set_secondary("Check again", self._show_devices)
            for child in self._device_card.winfo_children():
                child.destroy()
            found, reader, key_ok = result if result else ({"cameras": [], "chosen": None}, None, True)
            self._show_cameras(found)
            if reader is True:
                self._status_row(self._device_card, True, "Card reader found")
            elif reader is False:
                self._status_row(self._device_card, False, "No card reader found",
                                 "Plug in the NFC card reader. If it's plugged in and still not found, its USB ID "
                                 "needs adding to NFC_READER_USB_IDS in entry-agent\\.env (see README).")
            else:
                self._status_row(self._device_card, None, "Couldn't check for a card reader",
                                 "This only affects this check, not the reader itself.")
            if key_ok:
                self._status_row(self._device_card, True, "The gate monitor and the backend share the same key")
            else:
                row = self._status_row(self._device_card, False, "The gate monitor's key doesn't match the backend's",
                                       "The backend would turn every face check and card tap away.")
                ctk.CTkButton(row, text="Fix it", font=(FONT, 13, "bold"), height=34, width=80, corner_radius=8,
                              fg_color=MAROON, hover_color=MAROON_DEEP,
                              command=lambda: (device_setup.fix_gate_key(), self._show_devices())).pack(
                    side="right", anchor="n")

        self._background(work, done)

    def _show_cameras(self, found):
        """Which camera the gate monitor will use - a plugged-in (USB)
        camera before the laptop's built-in one (entry-agent/
        camera_select.py) - and the others it can see."""
        cameras = found["cameras"]
        if not cameras:
            self._status_row(self._device_card, False, "No camera found",
                             "Plug in the USB camera - the gate monitor picks it up by itself, before the "
                             "laptop's built-in camera.")
            return
        by_index = {index: (name, label) for index, name, label in cameras}
        name, label = by_index.get(found["chosen"], (cameras[0][1], cameras[0][2]))
        others = [f"{other} ({other_label})" for index, other, other_label in cameras
                  if index != found["chosen"]]
        detail = None
        ok = True
        if label == "built-in camera":
            detail = ("No plugged-in camera found, so this uses the laptop's own camera. Plug in the USB camera and "
                      "click Check again - it's used first whenever it's connected.")
        elif label in ("software camera", "infrared camera"):
            ok = False
            detail = "That isn't a real camera for the gate. Plug in the USB camera and click Check again."
        if others:
            detail = (detail + "\n" if detail else "") + "Also found: " + ", ".join(others) + "."
        self._status_row(self._device_card, ok, f"SecureTap will use: {name} ({label})", detail)

    # ---- 5. speed test ------------------------------------------------------------

    def _show_speed(self):
        self._step(4, "Speed test", "Run the speed test", self._run_speed_test)
        self._text(self.content, "SecureTap now measures how fast this computer runs the gate's face check, then "
                                 "picks the speed mode that keeps it from lagging. It takes about half a minute.",
                   INK_600)

    def _run_speed_test(self):
        self._show_speed()
        self._set_busy(True)
        label, log = self._progress("Testing this computer... (about half a minute)")
        self._log(log, "Loading the face models, then timing 20 face checks on a test photo.")

        def work():
            measured = device_setup.run_speed_test(self.python)
            return measured, device_setup.recommend(measured)

        def done(result, error):
            self._set_busy(False)
            label.bar.stop()
            if error:
                label.configure(text=str(error), text_color=DANGER)
                self._measured, self._recommended, self._reason = None, None, None
                self._show_speed_result()
                return
            (self._measured, (self._recommended, self._reason)) = result
            self._show_speed_result()

        self._background(work, done)

    def _show_speed_result(self):
        recommended = self._recommended or device_setup.DEFAULT_MODE
        self._step(4, "Speed test", "Finish", self._finish)
        self._set_secondary("Run again", self._run_speed_test)
        card = self._card()
        if self._measured:
            ctk.CTkLabel(card, text=f"{device_setup.MODES[recommended].label} mode", font=(SEMI_HEAVY, 24),
                         text_color=MAROON, anchor="w").pack(fill="x")
            self._text(card, self._reason, pady=(6, 0))
            measured = self._measured
            memory = f" · {measured['memory_gb']:.0f} GB memory" if measured.get("memory_gb") else ""
            self._text(card, f"Face check: {measured['ms_median']:.0f} ms · {measured['processors']} processor cores"
                             f"{memory}", INK_600, size=12, pady=(6, 0))
        else:
            self._status_row(card, False, "The speed test couldn't run",
                             "Pick a mode below - Standard is the normal one. You can run the test again later from "
                             "the launcher.")
        self._text(self.content, "Use this mode (you can change it any time in the launcher):", INK, bold=True,
                   pady=(18, 0))
        options = [(key, mode.label + ("  (recommended)" if key == recommended and self._measured else ""),
                    mode.summary) for key, mode in device_setup.MODES.items()]
        self._mode_choice = self._choices(options, recommended)

    def _finish(self):
        mode = self._mode_choice.get()
        chosen_by = "speed test" if self._measured and mode == self._recommended else "you"
        try:
            device_setup.save_profile(mode, chosen_by, self._measured, self._recommended, self._reason)
        except OSError as exc:
            messagebox.showerror("Couldn't save", f"Couldn't save this computer's settings: {exc}", parent=self.root)
            return
        self.completed = True
        for child in self.content.winfo_children():
            child.destroy()
        self.step_label.configure(text="Setup finished")
        ctk.CTkLabel(self.content, text="SecureTap is ready", font=(SEMI_HEAVY, 22), text_color=INK,
                     anchor="w").pack(fill="x")
        card = self._card()
        self._status_row(card, True, f"Speed mode: {device_setup.MODES[mode].label}")
        self._status_row(card, True, "Sign in to the dashboard with your Admin account",
                         "The launcher opens next - choose Dashboard or Entry Agent there.")
        self._set_secondary(None)
        self._set_primary("Open SecureTap", self.root.destroy)
