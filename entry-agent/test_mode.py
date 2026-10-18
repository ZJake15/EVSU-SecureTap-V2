"""The capstone speed/accuracy test mode - the gate monitor's side.

Measures how fast and how accurately the gate decides three kinds of faces
(an enrolled person, a spoof of them, an unregistered person) at 2-5 m, and
writes every trial to a CSV straight away. The most important rule: it only
ADDS measurement. Every frame goes through exactly the same scan, checks,
thresholds and voting as normal operation (the backend marks the entries
as test entries and adds measurement fields - backend/logs/test_mode.py).

How a trial is timed (all with time.perf_counter, in this one process):

    Start pressed --> frames... --> first frame with a face --> first USABLE
    frame (passed every quality check, so it was really compared) --> the
    first answer that's FINAL (it carries a record number, i.e. the voting
    agreed: Match / Unknown / Spoof, or a card tap was asked for)

- time_to_first_detection_ms = Start -> the answer that first had a face
- decision_time_ms (main metric) = capture of the first usable frame ->
  the final answer. Independent of how fast the subject turns: frames of a
  turning or moving head are skipped by the quality checks and don't start
  the clock.
- decision_time_from_first_face_ms = capture of the first frame with a
  face -> the final answer (includes turning/settling - the guard's view).

"Final" is measured when the answer reached the gate monitor, just before it
is drawn (the screen redraws every ~66 ms), so the guard sees it within
~0.1 s of the time written. A frame captured before Start is ignored.

Between trials, Start only becomes available once no face has been seen for
the voting window (decision_window_seconds, 4 s) - so no vote from the last
trial can carry over and make the next one look faster - and once a card-tap
request has expired.
"""

import csv
import json
import os
import random
import threading
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import customtkinter as ctk
import requests

from ui import (
    CANVAS, CAUTION, DANGER, FONT, FONT_MONO, INK, INK_600, LINE, MAROON, MAROON_DEEP, PROMPT, SURFACE, VERIFIED,
    WIDE_HEAVY,
)

CATEGORIES = ("enrolled", "spoof", "unknown")
DISTANCES_M = (2, 3, 4, 5)
ROUNDS = 10  # 10 trials per category per distance = 120
PLAN_SEED = 2026  # the trial order can be re-created exactly from this
EXPECTED = {"enrolled": "Match", "spoof": "Spoof", "unknown": "Unknown"}
DEFAULT_TIMEOUT_SECONDS = 10.0
DECIDED = ("Match", "Spoof", "Unknown", "Tiebreak requested")

PLAN_FIELDS = ["trial", "round", "category", "distance_m"]
RESULT_FIELDS = [
    "trial", "round", "session_id", "date_time", "category", "distance_m", "subject_label", "spoof_medium",
    "time_to_first_detection_ms", "decision_time_ms", "decision_time_from_first_face_ms",
    "frames_processed", "frames_with_face", "frames_skipped_turned", "frames_skipped_moving",
    "frames_skipped_blur", "frames_skipped_edge", "frames_covered", "frames_face_beyond_limit",
    "result", "expected_result", "correct", "repeat_entry", "matched_name", "matched_id", "match_confidence", "best_similarity",
    "liveness_score", "face_width_px", "face_height_px", "estimated_distance_m", "frame_size_sent",
    "camera_resolution", "note", "status",
]


# ---- the trial plan -------------------------------------------------------------


def make_plan(rounds=ROUNDS, seed=PLAN_SEED):
    """`rounds` rounds; each round has every category x distance once, in its
    own random order - balanced over time, so tiredness or a lighting change
    affects every condition alike."""
    rng = random.Random(seed)
    trials, number = [], 1
    for round_number in range(1, rounds + 1):
        block = [(category, distance) for category in CATEGORIES for distance in DISTANCES_M]
        rng.shuffle(block)
        for category, distance in block:
            trials.append({"trial": number, "round": round_number, "category": category, "distance_m": distance})
            number += 1
    return trials


def load_or_create_plan(folder):
    path = Path(folder) / "trial_plan.csv"
    if not path.exists():
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=PLAN_FIELDS)
            writer.writeheader()
            writer.writerows(make_plan())
    with open(path, encoding="utf-8") as fh:
        return [{"trial": int(r["trial"]), "round": int(r["round"]), "category": r["category"],
                 "distance_m": float(r["distance_m"])} for r in csv.DictReader(fh)]


def _fmt(value, digits=1):
    return "" if value is None else f"{value:.{digits}f}"


def _box_size(item):
    box = (item or {}).get("box") or {}
    if not box:
        return None, None
    return box["right"] - box["left"], box["bottom"] - box["top"]


def _width(item):
    return _box_size(item)[0] or 0


def _outcome(item):
    """The final result an answer carries for a face, or None if it's still
    being decided. Final = the voting agreed (a record number came back), or
    a card tap was asked for."""
    if item.get("tiebreak_required"):
        return "Tiebreak requested"
    if item.get("retry") or item.get("log_id") is None:
        return None
    if item.get("success"):
        return "Match"
    if item.get("spoof_suspected"):
        return "Spoof"
    return "Unknown"


# ---- one test session's trials ------------------------------------------------


class TestRun:
    """Everything the test does. on_frame is called from the scan thread,
    the rest from the Tk thread - all of it under one lock."""

    def __init__(self, folder, api_client, gate_location, describe_setup):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.results_path = self.folder / "results.csv"
        self.api = api_client
        self.gate_location = gate_location
        self.describe_setup = describe_setup  # () -> the gate monitor's own settings, for the snapshot
        self.plan = load_or_create_plan(self.folder)
        self._lock = threading.Lock()
        self.state = "setup"  # setup -> waiting <-> ready -> running -> waiting ... -> finished
        self.session = None
        self.quiet_seconds = 4.0
        self.card_wait_seconds = 10.0
        self.timeout_seconds = DEFAULT_TIMEOUT_SECONDS
        self.last_face_at = 0.0
        self.blocked_until = 0.0
        self.trial = None
        self.last_row = None
        self.message = ""

    # -- session --------------------------------------------------------------
    def begin_session(self, enrolled_id, enrolled_label, unknown_label, spoof_medium, timeout_seconds):
        """Checks the enrolled subject exists and saves the settings snapshot.
        Network - call off the Tk thread. Raises ValueError with a message."""
        snapshot = self.api.test_snapshot(enrolled_id)
        subject = snapshot.get("subject")
        if not subject:
            raise ValueError(f"No registered person has the ID number {enrolled_id!r}.")
        if not subject.get("is_active", True):
            raise ValueError(f"{subject['full_name']} is deactivated - the gate won't match them.")
        if not subject.get("face_photos"):
            raise ValueError(f"{subject['full_name']} has no face photos - enroll them first.")
        session_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        settings = snapshot.get("settings", {})
        session = {
            "session_id": session_id,
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "gate_location": self.gate_location,
            "plan": {"file": "trial_plan.csv", "trials": len(self.plan), "rounds": ROUNDS, "seed": PLAN_SEED,
                     "categories": list(CATEGORIES), "distances_m": list(DISTANCES_M)},
            "enrolled_subject": subject,
            "enrolled_label": enrolled_label, "unknown_label": unknown_label, "spoof_medium": spoof_medium,
            "timeout_seconds": timeout_seconds,
            "timing": "time.perf_counter on the gate monitor; final = first answer with a record number "
                      "(or a card-tap request); decision_time_ms starts at the first usable frame's capture",
            "gate_monitor": self.describe_setup(),
            "backend": snapshot.get("backend", {}),
            "settings": settings,
        }
        with open(self.folder / f"session_{session_id}_settings.json", "w", encoding="utf-8") as fh:
            json.dump(session, fh, indent=2, ensure_ascii=False, default=str)
        with self._lock:
            self.session = session
            self.quiet_seconds = float(settings.get("decision_window_seconds") or 4)
            self.card_wait_seconds = float(settings.get("card_wait_seconds") or 10)
            self.timeout_seconds = float(timeout_seconds)
            self.state = "waiting"
            self.message = "Session started - settings saved."
        return session

    # -- progress -----------------------------------------------------------------
    def _rows(self):
        if not self.results_path.exists():
            return []
        with open(self.results_path, encoding="utf-8") as fh:
            return list(csv.DictReader(fh))

    def _done_trials(self):
        return {int(r["trial"]) for r in self._rows() if r.get("status") == "final"}

    def current_plan_trial(self):
        """The first trial in the plan without a result - so a session can
        stop and carry on later where it left off."""
        done = self._done_trials()
        return next((t for t in self.plan if t["trial"] not in done), None)

    # -- frames from the scan loop -------------------------------------------------
    def on_frame(self, t_frame, t_answer, response, frame_size, camera_resolution):
        items = [r for r in response.get("results", []) if r.get("box")]
        with self._lock:
            if items:
                self.last_face_at = t_answer
            trial = self.trial
            if self.state != "running" or trial is None or t_frame < trial["t_start"]:
                return
            trial["frames"] += 1
            trial["frame_size"] = frame_size
            trial["camera_resolution"] = camera_resolution
            if not items:
                if response.get("test_faces_beyond_limit"):
                    trial["frames_beyond_limit"] += 1
                self._check_timeout(t_answer)
                return
            trial["frames_with_face"] += 1
            trial["last_face"] = max(items, key=_width)
            if trial["first_face"] is None:
                trial["first_face"] = (t_frame, t_answer)
            for item in items:
                if item.get("skip"):
                    trial["skips"][item["skip"]] += 1
            if trial["first_usable"] is None and any(not item.get("skip") for item in items):
                trial["first_usable"] = (t_frame, t_answer)
            if any(item.get("occlusion_suspected") for item in items):
                trial["frames_covered"] += 1
            finals = [item for item in items if _outcome(item)]
            if finals:
                final = max(finals, key=_width)  # the subject is the nearest (biggest) face
                self._finish(_outcome(final), t_answer, final)
            else:
                self._check_timeout(t_answer)

    def tick(self, now=None):
        """Called by the test window ten times a second."""
        now = time.perf_counter() if now is None else now
        with self._lock:
            if self.state == "running":
                self._check_timeout(now)
            elif self.state in ("waiting", "ready"):
                if self.current_plan_trial() is None:
                    self.state = "finished"
                elif now - self.last_face_at >= self.quiet_seconds and now >= self.blocked_until:
                    self.state = "ready"
                else:
                    self.state = "waiting"

    def _check_timeout(self, now):
        trial = self.trial
        if now - trial["t_start"] < self.timeout_seconds:
            return
        if trial["first_face"] is None:
            self._finish("Not detected", now, None)
        elif trial["frames_covered"] * 2 >= trial["frames_with_face"]:
            self._finish("Occluded", now, trial["last_face"])  # "please uncover" most of the time
        else:
            self._finish("No decision", now, trial["last_face"])

    # -- the tester's buttons --------------------------------------------------------
    def start(self):
        with self._lock:
            if self.state != "ready":
                return False
            plan_trial = self.current_plan_trial()
            if plan_trial is None:
                self.state = "finished"
                return False
            self.trial = {
                **plan_trial, "t_start": time.perf_counter(), "frames": 0, "frames_with_face": 0,
                "frames_covered": 0, "frames_beyond_limit": 0, "skips": Counter(), "first_face": None,
                "first_usable": None, "last_face": None, "frame_size": None, "camera_resolution": None,
            }
            self.state = "running"
            self.message = ""
            return True

    def redo(self):
        """While running: abandon this trial and do it again. After a trial:
        mark its row 'redone' (it stays in the file, for honesty) and do that
        trial again."""
        with self._lock:
            if self.state == "running":
                self.trial = None
                self.state = "waiting"
                self.message = "Trial abandoned - it will be done again."
                return True
            if self.last_row is None or self.state not in ("waiting", "ready", "finished"):
                return False
            rows = self._rows()
            for row in reversed(rows):
                if row["trial"] == str(self.last_row["trial"]) and row["status"] == "final":
                    row["status"] = "redone"
                    break
            self._rewrite(rows)
            self.message = f"Trial {self.last_row['trial']} marked 'redone' - it will be done again."
            self.last_row = None
            self.state = "waiting"
            return True

    def skip(self, note):
        with self._lock:
            if self.state not in ("waiting", "ready", "running"):
                return False
            plan_trial = self.trial if self.state == "running" else self.current_plan_trial()
            if plan_trial is None:
                return False
            row = self._row(plan_trial, result="Skipped", note=note or "skipped")
            self._append(row)
            self.last_row = row
            self.trial = None
            self.state = "waiting"
            self.message = f"Trial {plan_trial['trial']} skipped."
            return True

    # -- writing a trial ---------------------------------------------------------------
    def _finish(self, result, t_end, item):
        row = self._row(self.trial, result=result, item=item, t_end=t_end)
        self._append(row)
        self.last_row = row
        self.trial = None
        self.state = "waiting"
        if result == "Tiebreak requested":
            # The card-tap request stays open at this gate until it expires.
            self.blocked_until = t_end + self.card_wait_seconds + 1.0
        self.message = ""

    def _row(self, trial, result, item=None, t_end=None, note=""):
        session = self.session or {}
        category = trial["category"]
        expected = EXPECTED[category]
        subject = session.get("enrolled_subject") or {}
        item = item or {}
        width, height = _box_size(item)
        matched = result == "Match"
        matched_id = item.get("student_or_employee_id") if matched else None
        if result == "Skipped":
            correct = ""
        elif category == "enrolled":
            correct = "yes" if matched and matched_id == subject.get("student_or_employee_id") else "no"
        else:
            correct = "yes" if result == expected else "no"
        first_face = trial.get("first_face")
        first_usable = trial.get("first_usable")
        decided = t_end is not None and result in DECIDED
        similarity = item.get("similarity")
        frame_size = trial.get("frame_size")
        camera_resolution = trial.get("camera_resolution")
        skips = trial.get("skips") or Counter()
        return {
            "trial": trial["trial"], "round": trial["round"], "session_id": session.get("session_id", ""),
            "date_time": datetime.now().isoformat(sep=" ", timespec="milliseconds"),
            "category": category, "distance_m": f"{trial['distance_m']:g}",
            "subject_label": session.get("unknown_label") if category == "unknown" else session.get("enrolled_label"),
            "spoof_medium": session.get("spoof_medium") if category == "spoof" else "",
            "time_to_first_detection_ms": _fmt((first_face[1] - trial["t_start"]) * 1000) if first_face else "",
            "decision_time_ms": _fmt((t_end - first_usable[0]) * 1000) if decided and first_usable else "",
            "decision_time_from_first_face_ms": _fmt((t_end - first_face[0]) * 1000) if decided and first_face else "",
            "frames_processed": trial.get("frames", 0), "frames_with_face": trial.get("frames_with_face", 0),
            "frames_skipped_turned": skips["turned"], "frames_skipped_moving": skips["moving"],
            "frames_skipped_blur": skips["blur"], "frames_skipped_edge": skips["edge"],
            "frames_covered": trial.get("frames_covered", 0),
            "frames_face_beyond_limit": trial.get("frames_beyond_limit", 0),
            "result": result, "expected_result": expected, "correct": correct,
            # Decided by the same voting, but within the repeat-entry cooldown
            # of an earlier one, so it reused that record instead of a new one.
            "repeat_entry": ("yes" if item.get("deduped") else "no") if result in DECIDED else "",
            "matched_name": (item.get("person_name") or "") if matched else "",
            "matched_id": matched_id or "",
            "match_confidence": _fmt(similarity * 100) if matched and similarity is not None else "",
            "best_similarity": _fmt(similarity, 3),
            "liveness_score": _fmt(item.get("liveness_score"), 3),
            "face_width_px": "" if width is None else width, "face_height_px": "" if height is None else height,
            "estimated_distance_m": _fmt(item.get("test_distance_m"), 2),
            "frame_size_sent": f"{frame_size[0]}x{frame_size[1]}" if frame_size else "",
            "camera_resolution": f"{camera_resolution[0]}x{camera_resolution[1]}" if camera_resolution else "",
            "note": note, "status": "final",
        }

    def _append(self, row):
        new = not self.results_path.exists()
        with open(self.results_path, "a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=RESULT_FIELDS)
            if new:
                writer.writeheader()
            writer.writerow(row)
            fh.flush()
            os.fsync(fh.fileno())  # on disk before the next trial - nothing lost if the app crashes

    def _rewrite(self, rows):
        temporary = self.results_path.with_suffix(".tmp")
        with open(temporary, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=RESULT_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, self.results_path)

    # -- for the window ------------------------------------------------------------------
    def status(self):
        now = time.perf_counter()
        with self._lock:
            plan_trial = self.trial if self.state == "running" else self.current_plan_trial()
            info = {"state": self.state, "trial": plan_trial, "done": len(self._done_trials()),
                    "total": len(self.plan), "last_row": self.last_row, "message": self.message}
            if self.state == "running":
                info["elapsed"] = now - self.trial["t_start"]
                info["timeout"] = self.timeout_seconds
            elif self.state == "waiting":
                info["quiet_left"] = max(0.0, self.quiet_seconds - (now - self.last_face_at),
                                         self.blocked_until - now)
            return info


def describe_setup(config, camera, camera_name):
    """The gate monitor's own settings, for the session's settings snapshot."""
    return {
        "camera": camera_name, "camera_index": camera.index, "camera_zoom": camera.zoom,
        "camera_resolution": camera.capture_size(), "upload_max_dimension": config.upload_max_dimension,
        "scan_pause_seconds": config.scan_pause_seconds, "video_fps": config.video_fps,
        "app_version": config.app_version, "gate_location": config.gate_location,
    }


# ---- the window the tester watches ------------------------------------------------------


CATEGORY_COLOR = {"enrolled": VERIFIED, "spoof": DANGER, "unknown": CAUTION}


class TestWindow:
    """Large text: trial number, category and distance, what's happening, and
    the last result. Start / Redo / Skip are buttons and F8 / F9 / F10 - keys
    a card reader never types, so a tap can't press them."""

    def __init__(self, monitor, run, api_client, results_folder):
        master = monitor.window
        self.run = run
        self.api = api_client
        self.results_folder = str(results_folder)
        self.window = ctk.CTkToplevel(master)
        self.window.title("SecureTap - TEST MODE")
        self.window.geometry("780x700")
        self.window.configure(fg_color=CANVAS)
        self.window.protocol("WM_DELETE_WINDOW", lambda: None)  # closes with the gate monitor
        # The gate monitor keeps taking the keyboard back for the card reader
        # (and comes to the front when it does) - so this window stays on top,
        # and its text boxes are allowed to keep the keyboard while typing.
        self.window.attributes("-topmost", True)
        monitor.allow_typing_in(self.window)
        for widget in (master, self.window):
            widget.bind("<F8>", lambda _e: self._start(), add=True)
            widget.bind("<F9>", lambda _e: self._redo(), add=True)
            widget.bind("<F10>", lambda _e: (self._skip(), "break")[1], add=True)  # F10 also opens Windows' menu

        banner = ctk.CTkFrame(self.window, fg_color=DANGER, corner_radius=0, height=56)
        banner.pack(fill="x")
        ctk.CTkLabel(banner, text="TEST MODE  ·  results are saved automatically", font=(WIDE_HEAVY, 22),
                     text_color="white").pack(pady=12)

        self.setup = ctk.CTkFrame(self.window, fg_color=SURFACE, corner_radius=8)
        self.trials = ctk.CTkFrame(self.window, fg_color=SURFACE, corner_radius=8)
        self._build_setup()
        self._build_trials()
        self.setup.pack(fill="both", expand=True, padx=16, pady=16)
        self._poll()

    # -- session setup ------------------------------------------------------------
    def _build_setup(self):
        f = self.setup
        ctk.CTkLabel(f, text="Start a test session", font=(FONT, 22, "bold"), text_color=INK).pack(
            anchor="w", padx=20, pady=(18, 4))
        ctk.CTkLabel(f, text=f"Trial plan: {len(self.run.plan)} trials ({ROUNDS} balanced rounds, seed {PLAN_SEED}). "
                             f"Results are saved in {self.results_folder}", font=(FONT, 13), text_color=INK_600,
                     wraplength=700, justify="left").pack(anchor="w", padx=20)
        self.fields = {}
        for key, label, default in (
            ("enrolled_id", "Enrolled subject's ID number (must be registered with face photos)", ""),
            ("enrolled_label", "Enrolled subject label (as it should appear in the paper)", "Subject A"),
            ("unknown_label", "Unknown subject label (an unregistered volunteer)", "Volunteer 1"),
            ("timeout", "Timeout per trial (seconds)", f"{DEFAULT_TIMEOUT_SECONDS:g}"),
        ):
            ctk.CTkLabel(f, text=label, font=(FONT, 14, "bold"), text_color=INK).pack(anchor="w", padx=20,
                                                                                     pady=(12, 2))
            entry = ctk.CTkEntry(f, font=(FONT, 15), height=36)
            entry.insert(0, default)
            entry.pack(fill="x", padx=20)
            self.fields[key] = entry
        ctk.CTkLabel(f, text="Spoof medium", font=(FONT, 14, "bold"), text_color=INK).pack(anchor="w", padx=20,
                                                                                         pady=(12, 2))
        self.medium = ctk.CTkSegmentedButton(f, values=["printed photo", "phone screen", "tablet screen"],
                                             font=(FONT, 14))
        self.medium.set("printed photo")
        self.medium.pack(anchor="w", padx=20)
        self.setup_error = ctk.CTkLabel(f, text="", font=(FONT, 14), text_color=DANGER, wraplength=700,
                                        justify="left")
        self.setup_error.pack(anchor="w", padx=20, pady=(10, 0))
        self.begin = ctk.CTkButton(f, text="Begin session", font=(FONT, 16, "bold"), height=44, fg_color=MAROON,
                                   hover_color=MAROON_DEEP, command=self._begin)
        self.begin.pack(anchor="w", padx=20, pady=16)

    def _begin(self):
        values = {key: entry.get().strip() for key, entry in self.fields.items()}
        try:
            timeout = float(values["timeout"])
        except ValueError:
            timeout = 0
        if not 3 <= timeout <= 60:
            self.setup_error.configure(text="The timeout must be a number of seconds between 3 and 60.")
            return
        if not values["enrolled_id"]:
            self.setup_error.configure(text="Type the enrolled subject's ID number.")
            return
        self.begin.configure(state="disabled", text="Checking…")
        self.setup_error.configure(text="")

        def work():
            try:
                self.run.begin_session(values["enrolled_id"], values["enrolled_label"] or "Subject A",
                                       values["unknown_label"] or "Volunteer 1", self.medium.get(), timeout)
                self.window.after(0, self._show_trials)
            except (ValueError, requests.RequestException) as exc:
                message = str(exc)
                self.window.after(0, lambda: self._setup_failed(message))

        threading.Thread(target=work, daemon=True).start()

    def _setup_failed(self, message):
        self.begin.configure(state="normal", text="Begin session")
        self.setup_error.configure(text=message)

    def _show_trials(self):
        self.setup.pack_forget()
        self.trials.pack(fill="both", expand=True, padx=16, pady=16)
        self._refresh()

    # -- trials -------------------------------------------------------------------------
    def _build_trials(self):
        f = self.trials
        self.counter = ctk.CTkLabel(f, text="", font=(FONT, 20, "bold"), text_color=INK_600)
        self.counter.pack(pady=(16, 0))
        self.category = ctk.CTkLabel(f, text="", font=(WIDE_HEAVY, 60), text_color=INK)
        self.category.pack()
        self.distance = ctk.CTkLabel(f, text="", font=(WIDE_HEAVY, 72), text_color=INK)
        self.distance.pack()
        self.state_label = ctk.CTkLabel(f, text="", font=(FONT, 20, "bold"), text_color=INK, wraplength=720)
        self.state_label.pack(pady=(6, 0))
        self.result_label = ctk.CTkLabel(f, text="", font=(FONT_MONO, 15), text_color=INK_600, wraplength=720,
                                         justify="center")
        self.result_label.pack(pady=(6, 0))
        buttons = ctk.CTkFrame(f, fg_color="transparent")
        buttons.pack(pady=14)
        self.start_button = ctk.CTkButton(buttons, text="Start  (F8)", font=(FONT, 18, "bold"), width=200, height=52,
                                          fg_color=VERIFIED, command=self._start)
        self.start_button.pack(side="left", padx=6)
        ctk.CTkButton(buttons, text="Redo  (F9)", font=(FONT, 16), width=150, height=52, fg_color=PROMPT,
                      command=self._redo).pack(side="left", padx=6)
        ctk.CTkButton(buttons, text="Skip  (F10)", font=(FONT, 16), width=150, height=52, fg_color=INK_600,
                      command=self._skip).pack(side="left", padx=6)
        note_row = ctk.CTkFrame(f, fg_color="transparent")
        note_row.pack(fill="x", padx=20)
        ctk.CTkLabel(note_row, text="Skip note (why)", font=(FONT, 13, "bold"), text_color=INK).pack(side="left")
        self.skip_note = ctk.CTkEntry(note_row, font=(FONT, 14), height=32,
                                      placeholder_text="e.g. subject not available - type, then Skip")
        self.skip_note.pack(side="left", fill="x", expand=True, padx=(10, 0))
        footer = ctk.CTkFrame(f, fg_color="transparent")
        footer.pack(fill="x", side="bottom", pady=(0, 12))
        ctk.CTkButton(footer, text="Open results folder", font=(FONT, 13), width=170, fg_color=LINE,
                      text_color=INK, hover_color=CANVAS, command=lambda: os.startfile(self.results_folder)).pack(
            side="left", padx=16)
        ctk.CTkButton(footer, text="Delete all test data…", font=(FONT, 13), width=190, fg_color=DANGER,
                      command=self._delete_test_data).pack(side="right", padx=16)

    def _start(self):
        if self.run.start():
            self._refresh()

    def _redo(self):
        if self.run.redo():
            self._refresh()

    def _skip(self):
        info = self.run.status()
        if info["state"] not in ("waiting", "ready", "running") or info["trial"] is None:
            return
        note = self.skip_note.get().strip()
        if not note:
            self.result_label.configure(text="To skip: type why in the Skip note box, then press Skip again.")
            self.skip_note.focus_set()
            return
        if self.run.skip(note):
            self.skip_note.delete(0, "end")
            self.window.focus_set()  # the keyboard goes back to the card reader
            self._refresh()

    def _delete_test_data(self):
        from tkinter import messagebox

        if not messagebox.askyesno(
            "Delete all test data?",
            "This deletes every TEST entry and its photo from SecureTap, and the face data the scan kept "
            "during the test sessions (including the unknown volunteer's).\n\nReal entries are not touched, "
            "and the results CSV files stay in the results folder.\n\nDelete now?", parent=self.window,
        ):
            return

        def work():
            try:
                counts = self.api.delete_test_data().get("deleted", {})
                text = "Deleted: " + ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in counts.items())
            except requests.RequestException as exc:
                text = f"Could not delete: {exc}"
            self.window.after(0, lambda: messagebox.showinfo("Test data", text, parent=self.window))

        threading.Thread(target=work, daemon=True).start()

    def _poll(self):
        try:
            self.run.tick()
            self._refresh()
        finally:
            self.window.after(100, self._poll)

    def _refresh(self):
        info = self.run.status()
        state = info["state"]
        if state == "setup":
            return
        trial = info["trial"]
        if trial:
            self.counter.configure(text=f"Trial {trial['trial']} of {info['total']}  ·  round {trial['round']}  ·  "
                                        f"{info['done']} done")
            self.category.configure(text=trial["category"].upper(), text_color=CATEGORY_COLOR[trial["category"]])
            self.distance.configure(text=f"{trial['distance_m']:g} m")
        if state == "finished":
            self.counter.configure(text=f"{info['done']} of {info['total']} done")
            self.category.configure(text="ALL DONE", text_color=VERIFIED)
            self.distance.configure(text="")
            self.state_label.configure(text="Every trial in the plan has a result.", text_color=VERIFIED)
            self.start_button.configure(state="disabled")
        elif state == "running":
            self.state_label.configure(text=f"Running…  {info['elapsed']:.1f} s  (timeout {info['timeout']:g} s)",
                                       text_color=PROMPT)
            self.start_button.configure(state="disabled")
        elif state == "ready":
            self.state_label.configure(text="READY - subject on the mark, facing away. Press Start (F8), "
                                            "then they turn to the camera.", text_color=VERIFIED)
            self.start_button.configure(state="normal")
        else:
            self.state_label.configure(text=f"Wait… no face for {info.get('quiet_left', 0):.1f} s more "
                                            "(so the last trial can't carry over)", text_color=CAUTION)
            self.start_button.configure(state="disabled")
        row = info["last_row"]
        if info["message"]:
            self.result_label.configure(text=info["message"])
        elif row:
            mark = {"yes": "✓ correct", "no": "✗ not correct"}.get(row["correct"], "")
            timing = f"decision {row['decision_time_ms']} ms" if row["decision_time_ms"] else "no decision time"
            who = f" · {row['matched_name']} {row['match_confidence']}%" if row["matched_name"] else ""
            self.result_label.configure(
                text=f"Last: trial {row['trial']} ({row['category']}, {row['distance_m']} m) → {row['result']}"
                     f"{who}  {mark}\n{timing} · first face {row['time_to_first_detection_ms'] or '-'} ms · "
                     f"{row['frames_processed']} frames · face {row['face_width_px']}x{row['face_height_px']} px"
            )
        else:
            self.result_label.configure(text="")
