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

import os
import re
import shutil
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from collections import deque
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk
import requests

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


class ManagedProcess:
    """One child process the launcher owns: how to start it, whether it's
    still alive, and how to stop it and everything it spawned."""

    def __init__(self, label, on_output=None):
        self.label = label
        self.process = None
        self._on_output = on_output

    def is_running(self):
        return self.process is not None and self.process.poll() is None

    def start(self, args, cwd):
        if self.is_running():
            return True
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

        self.backend = ManagedProcess("backend", self._append_log)
        self.dashboard = ManagedProcess("dashboard", self._handle_dashboard_output)
        self.entry_agent = ManagedProcess("entry-agent", self._handle_entry_agent_output)

        self.root = ctk.CTk()
        self.root.title("EVSU SecureTap")
        self.root.configure(fg_color=BG)
        self.root.geometry("560x680")
        self.root.minsize(520, 620)
        self.root.protocol("WM_DELETE_WINDOW", self._handle_quit)
        _apply_icon(self.root)

        HeaderBar(
            self.root, "EVSU SecureTap", "Choose what to open",
            center=True, logo_path=ICON_PATH,
        ).pack(fill="x")

        self._build_status_row()
        ctk.CTkFrame(self.root, fg_color=BORDER, height=1).pack(fill="x")
        self._build_buttons()
        self._build_log_panel()
        self._build_footer()

        self._start_backend()
        self.root.after(500, self._poll_health)
        self.root.after(LOG_REFRESH_MS, self._flush_log)

    # ---- layout ---------------------------------------------------------

    def _build_status_row(self):
        wrap = ctk.CTkFrame(self.root, fg_color="transparent")
        wrap.pack(fill="x")
        row = ctk.CTkFrame(wrap, fg_color="transparent")
        row.pack(pady=16)

        self.status_labels = {}
        for key, label in (("backend", "Backend"), ("dashboard", "Dashboard"), ("entry-agent", "Entry agent")):
            item = ctk.CTkLabel(row, text=f"● {label}", font=(FONT, 11), text_color=TEXT_MUTED)
            item.pack(side="left", padx=14)
            self.status_labels[key] = (item, label)

    def _build_buttons(self):
        buttons = ctk.CTkFrame(self.root, fg_color="transparent")
        buttons.pack(fill="x", padx=28, pady=(24, 8))

        self.dashboard_button, self.dashboard_subtitle = self._choice_button(
            buttons, "Dashboard", DASHBOARD_IDLE_SUBTITLE, self._open_dashboard,
        )
        self.dashboard_button.pack(fill="x", pady=(0, 16))

        self.entry_agent_button, self.entry_agent_subtitle = self._choice_button(
            buttons, "Entry Agent", ENTRY_AGENT_IDLE_SUBTITLE, self._open_entry_agent,
        )
        self.entry_agent_button.pack(fill="x")

    def _choice_button(self, parent, title, subtitle, command):
        """A clickable maroon card - a plain CTkFrame with a click binding
        rather than CTkButton, since CTkButton's single `text` can't render a
        bold title above a lighter subtitle. Same construction as the gate
        monitor's launcher card, so the two read as one app."""
        card = ctk.CTkFrame(parent, fg_color=MAROON, corner_radius=16, cursor="hand2")
        inner = ctk.CTkFrame(card, fg_color="transparent", cursor="hand2")
        inner.pack(fill="both", expand=True, padx=24, pady=18)
        title_label = ctk.CTkLabel(
            inner, text=title, font=(FONT, 18, "bold"), text_color="white", anchor="w", cursor="hand2"
        )
        title_label.pack(fill="x")
        subtitle_label = ctk.CTkLabel(
            inner, text=subtitle, font=(FONT, 12), text_color=MAROON_LIGHT,
            anchor="w", cursor="hand2", justify="left",
        )
        subtitle_label.pack(fill="x", pady=(4, 0))

        animator = _HoverAnimator(card, MAROON, MAROON_DARK)
        self._hover_animators.append(animator)  # keep a reference alive

        for widget in (card, inner, title_label, subtitle_label):
            widget.bind("<Button-1>", lambda _event: command())
            widget.bind("<Enter>", animator.enter)
            widget.bind("<Leave>", animator.leave)
        return card, subtitle_label

    def _build_log_panel(self):
        wrap = ctk.CTkFrame(self.root, fg_color="transparent")
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
        ctk.CTkFrame(self.root, fg_color=BORDER, height=1).pack(fill="x", pady=(16, 0))
        footer = ctk.CTkFrame(self.root, fg_color="transparent")
        footer.pack(fill="x", padx=28, pady=14)
        ctk.CTkLabel(
            footer, text="Quitting stops everything this window started.",
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
        self._append_log("[launcher] starting entry-agent")
        self._entry_agent_ready = False
        self.entry_agent.start([venv_python(), "-u", "main.py"], ENTRY_AGENT_DIR)
        self._set_status("entry-agent", "starting", "Entry agent starting")
        # Names what's actually taking the time, so the wait reads as work
        # rather than as the button having missed the click.
        self._start_spinner(
            "entry-agent", self.entry_agent_subtitle, "Starting the camera and opening the gate monitor..."
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
        label.configure(text=f"● {text}", text_color=colors.get(state, TEXT_MUTED))

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
