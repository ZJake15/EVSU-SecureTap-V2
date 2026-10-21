"""The capstone speed/accuracy test mode - the gate monitor's side.

Measures how fast and how accurately the gate decides three kinds of faces
(an enrolled person, a spoof of them, an unregistered person) at 2-3.5 m, and
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
import ctypes
import json
import os
import random
import statistics
import threading
import time
from collections import Counter
from ctypes import wintypes
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
# The default number of trials per category per distance (3 x 4 x 20 = 240);
# the session form can set fewer, for a pilot. One round = every category x
# distance once, so this is also the number of rounds.
ROUNDS = 20
PARTS = ("All", "Part 1", "Part 2")
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
    "sharpness_min", "sharpness_median", "sharpness_max",
    # Where the time went (see "Speed" below): per trial, the middle value of
    # its frames, and the slowest answer; screen freezes; how busy the
    # computer was.
    "capture_ms_median", "answer_ms_median", "answer_ms_max", "server_ms_median", "face_ai_ms_median",
    "fake_check_ms_median", "screen_freezes", "longest_freeze_ms", "cpu_max_pct", "memory_max_pct",
    "result", "expected_result", "correct", "repeat_entry", "matched_name", "matched_id", "match_confidence", "best_similarity",
    "liveness_score", "liveness_model_27", "liveness_model_40", "liveness_classical", "face_width_px", "face_height_px", "estimated_distance_m", "frame_size_sent",
    "camera_resolution", "note", "status",
]


# ---- speed: where each frame's time goes, and screen freezes ------------------------
#
# Per frame: capture = taking + encoding the picture on the gate laptop;
# answer = sending it until the answer is back (network + backend + building
# the result); server = the backend's own part of that; face AI = finding the
# faces and their fingerprints; fake check = the liveness model. A "screen
# freeze" is the gate monitor's window not updating for FREEZE_MS or more -
# every one, and every answer slower than SLOW_ANSWER_MS, goes into
# performance_log.csv with how busy the computer was at that moment.
POLL_MS = 100
FREEZE_MS = 500
SLOW_ANSWER_MS = 1500
PERF_FIELDS = ["date_time", "event", "duration_ms", "trial", "cpu_pct", "memory_pct"]


class _MemoryStatus(ctypes.Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD), ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class SystemMeter:
    """How busy the whole computer is - CPU in use (%) since the last reading
    and memory in use (%) - from Windows itself, no extra library. None where
    Windows doesn't say."""

    def __init__(self):
        self._last = self._cpu_times()

    @staticmethod
    def _cpu_times():
        try:
            idle, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
            if ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
                as_int = [(t.dwHighDateTime << 32) | t.dwLowDateTime for t in (idle, kernel, user)]
                return as_int[0], as_int[1] + as_int[2]  # kernel time includes idle time
        except (AttributeError, OSError):
            pass
        return None

    def read(self):
        now = self._cpu_times()
        cpu = None
        if now and self._last and now[1] > self._last[1]:
            cpu = round(100 * (1 - (now[0] - self._last[0]) / (now[1] - self._last[1])), 1)
        self._last = now or self._last
        memory = None
        try:
            status = _MemoryStatus()
            status.dwLength = ctypes.sizeof(_MemoryStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                memory = int(status.dwMemoryLoad)
        except (AttributeError, OSError):
            pass
        return cpu, memory


def _median(values, digits=1):
    return _fmt(statistics.median(values), digits) if values else ""


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


def load_plan(folder):
    """The folder's trial_plan.csv, or [] if there's none yet."""
    path = Path(folder) / "trial_plan.csv"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as fh:
        return [{"trial": int(r["trial"]), "round": int(r["round"]), "category": r["category"],
                 "distance_m": float(r["distance_m"])} for r in csv.DictReader(fh)]


def write_plan(folder, plan):
    with open(Path(folder) / "trial_plan.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=PLAN_FIELDS)
        writer.writeheader()
        writer.writerows(plan)


def describe_plan(plan):
    if not plan:
        return "no plan yet"
    distances = ", ".join(f"{d:g}" for d in sorted({t["distance_m"] for t in plan}))
    return f"{max(t['round'] for t in plan)} trials per condition at {distances} m ({len(plan)} trials)"


def part_rounds(part, rounds):
    """The rounds a session part covers: Part 1 = the first half of the
    rounds, Part 2 = the second half. Each round has every category x
    distance exactly once, so each half has an equal share of every
    condition."""
    half = rounds // 2
    if part == "Part 1":
        return set(range(1, half + 1))
    if part == "Part 2":
        return set(range(half + 1, rounds + 1))
    return set(range(1, rounds + 1))


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
        self.perf_path = self.folder / "performance_log.csv"
        self.meter = SystemMeter()  # read only by the test window (Tk thread)
        self.last_system = (None, None)  # its latest (CPU %, memory %)
        self.api = api_client
        self.gate_location = gate_location
        self.describe_setup = describe_setup  # () -> the gate monitor's own settings, for the snapshot
        self.plan = load_plan(self.folder)  # made (or checked) when a session begins
        self.part = "All"
        self.rounds_in_part = {t["round"] for t in self.plan}
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
    def _prepare_plan(self, rounds, part):
        """The plan for `rounds` trials per condition - the existing one if it
        matches, a new one if there's none or no results yet. A different plan
        with results already in the folder is refused (archive it first)."""
        if part != "All" and rounds % 2:
            raise ValueError("To split the test into Part 1 and Part 2, trials per condition must be an even "
                             "number (each part gets half of every condition).")
        plan = load_plan(self.folder)
        fits = (bool(plan) and max(t["round"] for t in plan) == rounds
                and sorted({t["distance_m"] for t in plan}) == sorted(float(d) for d in DISTANCES_M)
                and {t["category"] for t in plan} == set(CATEGORIES))
        if not fits:
            if self._rows():
                raise ValueError(
                    f"The results folder already has results for another plan ({describe_plan(plan)}). "
                    "Press 'Start a new test…' to move them into an archive folder first - or set Trials per "
                    "condition to match that plan to carry on with it."
                )
            plan = make_plan(rounds)
            write_plan(self.folder, plan)
        return plan, part_rounds(part, rounds)

    def archive(self):
        """'Start a new test': moves this folder's results, plan and session
        snapshots into old-run-<date-time> (nothing is deleted). Returns that
        folder, or None if there was nothing to move. Only before a session."""
        with self._lock:
            if self.state != "setup":
                return None
            files = [p for p in (self.results_path, self.folder / "trial_plan.csv") if p.exists()]
            files += sorted(self.folder.glob("session_*_settings.json"))
            if not files:
                return None
            destination = self.folder / f"old-run-{datetime.now():%Y%m%d-%H%M%S}"
            destination.mkdir()
            for path in files:
                path.rename(destination / path.name)
            self.plan, self.rounds_in_part, self.last_row = [], set(), None
            return destination

    def folder_summary(self):
        """One line for the session form: what's in the results folder now."""
        done = len(self._done_trials())
        plan = load_plan(self.folder)
        if not plan:
            return "The results folder is empty - a new plan is made when the session begins."
        return f"In the results folder: a plan of {describe_plan(plan)}, {done} trials done."

    def begin_session(self, enrolled_id, enrolled_label, unknown_label, spoof_medium, timeout_seconds,
                      trials_per_condition=ROUNDS, part="All"):
        """Checks the plan and the enrolled subject, and saves the settings
        snapshot. Network - call off the Tk thread. Raises ValueError with a
        message."""
        plan, rounds_in_part = self._prepare_plan(int(trials_per_condition), part)
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
            "plan": {"file": "trial_plan.csv", "trials": len(plan), "trials_per_condition": int(trials_per_condition),
                     "seed": PLAN_SEED, "categories": list(CATEGORIES), "distances_m": list(DISTANCES_M),
                     "part": part, "rounds_in_part": sorted(rounds_in_part),
                     "trials_in_part": sum(1 for t in plan if t["round"] in rounds_in_part)},
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
            self.plan, self.part, self.rounds_in_part = plan, part, rounds_in_part
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
        """The first trial in this session's part of the plan without a
        result - so a session can stop and carry on later where it left off."""
        done = self._done_trials()
        return next((t for t in self.plan if t["round"] in self.rounds_in_part and t["trial"] not in done), None)

    def _part_progress(self):
        done = self._done_trials()
        in_part = [t["trial"] for t in self.plan if t["round"] in self.rounds_in_part]
        return sum(1 for trial in in_part if trial in done), len(in_part)

    # -- frames from the scan loop -------------------------------------------------
    def on_frame(self, t_frame, t_answer, response, frame_size, camera_resolution, t_sent=None):
        items = [r for r in response.get("results", []) if r.get("box")]
        with self._lock:
            if items:
                self.last_face_at = t_answer
            # Logged the whole time test mode is open, not only during a
            # trial - "the results come late" can happen any time.
            if t_sent is not None and (t_answer - t_sent) * 1000 >= SLOW_ANSWER_MS:
                self._log_perf("slow answer", (t_answer - t_sent) * 1000, *self.last_system)
            trial = self.trial
            if self.state != "running" or trial is None or t_frame < trial["t_start"]:
                return
            trial["frames"] += 1
            if t_sent is not None:
                trial["capture_ms"].append((t_sent - t_frame) * 1000)
                trial["answer_ms"].append((t_answer - t_sent) * 1000)
            timing = response.get("test_timing") or {}
            for key, field in (("server_ms", "server_ms"), ("faces_ms", "faces_ms"), ("fake_ms", "fake_check_ms")):
                if timing.get(field) is not None:
                    trial[key].append(float(timing[field]))
            trial["frame_size"] = frame_size
            trial["camera_resolution"] = camera_resolution
            if not items:
                if response.get("test_faces_beyond_limit"):
                    trial["frames_beyond_limit"] += 1
                self._check_timeout(t_answer)
                return
            trial["frames_with_face"] += 1
            trial["last_face"] = max(items, key=_width)
            if trial["last_face"].get("test_sharpness") is not None:
                trial["sharpness"].append(trial["last_face"]["test_sharpness"])
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
                "sharpness": [], "capture_ms": [], "answer_ms": [], "server_ms": [], "faces_ms": [], "fake_ms": [],
                "freezes": [], "cpu": [], "memory": [],
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

    # -- speed ----------------------------------------------------------------------------
    def sample_system(self):
        """About once a second, from the test window: how busy the computer is."""
        cpu, memory = self.last_system = self.meter.read()
        with self._lock:
            if self.state == "running" and self.trial is not None:
                if cpu is not None:
                    self.trial["cpu"].append(cpu)
                if memory is not None:
                    self.trial["memory"].append(memory)

    def note_freeze(self, duration_ms):
        """The gate monitor's window didn't update for duration_ms (the test
        window shares its screen loop, so it noticed)."""
        cpu, memory = self.last_system = self.meter.read()  # over the frozen moment
        with self._lock:
            if self.state == "running" and self.trial is not None:
                self.trial["freezes"].append(duration_ms)
                if cpu is not None:
                    self.trial["cpu"].append(cpu)
                if memory is not None:
                    self.trial["memory"].append(memory)
            self._log_perf("screen freeze", duration_ms, cpu, memory)

    def _log_perf(self, event, duration_ms, cpu, memory):
        """One line in performance_log.csv - caller holds the lock."""
        new = not self.perf_path.exists()
        trial = self.trial["trial"] if self.state == "running" and self.trial else ""
        with open(self.perf_path, "a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=PERF_FIELDS)
            if new:
                writer.writeheader()
            writer.writerow({
                "date_time": datetime.now().isoformat(sep=" ", timespec="milliseconds"), "event": event,
                "duration_ms": _fmt(duration_ms, 0), "trial": trial,
                "cpu_pct": "" if cpu is None else _fmt(cpu, 0), "memory_pct": "" if memory is None else memory,
            })

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
        sharp = trial.get("sharpness") or []
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
            # The blur check's score of the subject's face in this trial's
            # frames - frames under "Blur sensitivity" were skipped as blurry.
            "sharpness_min": _fmt(min(sharp)) if sharp else "",
            "sharpness_median": _fmt(statistics.median(sharp)) if sharp else "",
            "sharpness_max": _fmt(max(sharp)) if sharp else "",
            "capture_ms_median": _median(trial.get("capture_ms")),
            "answer_ms_median": _median(trial.get("answer_ms")),
            "answer_ms_max": _fmt(max(trial["answer_ms"])) if trial.get("answer_ms") else "",
            "server_ms_median": _median(trial.get("server_ms")),
            "face_ai_ms_median": _median(trial.get("faces_ms")),
            "fake_check_ms_median": _median(trial.get("fake_ms")),
            "screen_freezes": len(trial.get("freezes") or []),
            "longest_freeze_ms": _fmt(max(trial["freezes"]), 0) if trial.get("freezes") else "",
            "cpu_max_pct": _fmt(max(trial["cpu"]), 0) if trial.get("cpu") else "",
            "memory_max_pct": max(trial["memory"]) if trial.get("memory") else "",
            "result": result, "expected_result": expected, "correct": correct,
            # Decided by the same voting, but within the repeat-entry cooldown
            # of an earlier one, so it reused that record instead of a new one.
            "repeat_entry": ("yes" if item.get("deduped") else "no") if result in DECIDED else "",
            "matched_name": (item.get("person_name") or "") if matched else "",
            "matched_id": matched_id or "",
            "match_confidence": _fmt(similarity * 100) if matched and similarity is not None else "",
            "best_similarity": _fmt(similarity, 3),
            "liveness_score": _fmt(item.get("liveness_score"), 3),
            # The fake-face score's parts: each trained model's own "real"
            # score and the texture/frequency/reflectance cues.
            "liveness_model_27": _fmt((item.get("test_liveness_parts") or {}).get("2.7"), 3),
            "liveness_model_40": _fmt((item.get("test_liveness_parts") or {}).get("4.0"), 3),
            "liveness_classical": _fmt((item.get("test_liveness_parts") or {}).get("classical"), 3),
            "face_width_px": "" if width is None else width, "face_height_px": "" if height is None else height,
            "estimated_distance_m": _fmt(item.get("test_distance_m"), 2),
            "frame_size_sent": f"{frame_size[0]}x{frame_size[1]}" if frame_size else "",
            "camera_resolution": f"{camera_resolution[0]}x{camera_resolution[1]}" if camera_resolution else "",
            "note": note, "status": "final",
        }

    def _append(self, row):
        new = not self.results_path.exists()
        if not new:
            with open(self.results_path, encoding="utf-8") as fh:
                header = next(csv.reader(fh), [])
            if header != RESULT_FIELDS:
                # Written by an older version with fewer columns - rewrite
                # it once with today's header (old rows keep their values).
                self._rewrite(self._rows())
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
            done, total = self._part_progress()
            info = {"state": self.state, "trial": plan_trial, "done": done, "total": total, "part": self.part,
                    "plan_total": len(self.plan), "last_row": self.last_row, "message": self.message}
            if self.state == "running":
                info["elapsed"] = now - self.trial["t_start"]
                info["timeout"] = self.timeout_seconds
            elif self.state == "waiting":
                info["quiet_left"] = max(0.0, self.quiet_seconds - (now - self.last_face_at),
                                         self.blocked_until - now)
                # Frames come every ~0.3-0.5 s, so a face within the last
                # second means one is in view right now.
                info["face_in_view"] = now - self.last_face_at < 1.0
                info["card_wait"] = self.blocked_until > now
            return info


def describe_setup(config, camera, camera_name, report=None):
    """The gate monitor's own settings, for the session's settings snapshot -
    the values it actually runs with (speed mode, then Settings > Camera),
    from main.camera_report when given."""
    report = report or {}
    return {
        "camera": camera_name, "camera_index": camera.index, "camera_zoom": camera.zoom,
        "camera_resolution": camera.capture_size(),
        "upload_max_dimension": report.get("upload_size", config.upload_max_dimension),
        "scan_pause_seconds": report.get("scan_pause", config.scan_pause_seconds),
        "video_fps": report.get("video_fps", config.video_fps),
        "app_version": config.app_version, "gate_location": config.gate_location,
        "camera_report": report,
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

        # Scrolls, so the whole form fits a 768 px-tall laptop screen.
        self.setup = ctk.CTkScrollableFrame(self.window, fg_color=SURFACE, corner_radius=8)
        self.trials = ctk.CTkFrame(self.window, fg_color=SURFACE, corner_radius=8)
        self._build_setup()
        self._build_trials()
        self.setup.pack(fill="both", expand=True, padx=16, pady=16)
        self._last_poll = None
        self._last_sample = 0.0
        self._poll()

    # -- session setup ------------------------------------------------------------
    def _build_setup(self):
        f = self.setup
        ctk.CTkLabel(f, text="Start a test session", font=(FONT, 22, "bold"), text_color=INK).pack(
            anchor="w", padx=20, pady=(18, 4))
        ctk.CTkLabel(f, text=f"Distances {', '.join(f'{d:g}' for d in DISTANCES_M)} m · 3 categories · mixed, "
                             f"balanced order (seed {PLAN_SEED}). Results are saved in {self.results_folder}",
                     font=(FONT, 13), text_color=INK_600, wraplength=680, justify="left").pack(anchor="w", padx=20)
        folder_row = ctk.CTkFrame(f, fg_color="transparent")
        folder_row.pack(fill="x", padx=20, pady=(8, 0))
        self.folder_label = ctk.CTkLabel(folder_row, text="", font=(FONT, 13, "bold"), text_color=INK,
                                         wraplength=500, justify="left")
        self.folder_label.pack(side="left")
        ctk.CTkButton(folder_row, text="Start a new test…", font=(FONT, 13), width=150, fg_color=LINE,
                      text_color=INK, hover_color=CANVAS, command=self._new_test).pack(side="right")
        self._show_folder_summary()
        self.fields = {}
        for key, label, default in (
            ("enrolled_id", "Enrolled subject's ID number (must be registered with face photos)", ""),
            ("enrolled_label", "Enrolled subject label (as it should appear in the paper)", "Subject A"),
            ("unknown_label", "Unknown subject label (an unregistered volunteer)", "Volunteer 1"),
            ("trials_per_condition", f"Trials per category per distance ({ROUNDS} for the full test - fewer for "
                                     "a pilot; same number every session of one test)", f"{ROUNDS}"),
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
        ctk.CTkLabel(f, text="This session runs", font=(FONT, 14, "bold"), text_color=INK).pack(
            anchor="w", padx=20, pady=(12, 2))
        self.part = ctk.CTkSegmentedButton(f, values=list(PARTS), font=(FONT, 14))
        self.part.set("All")
        self.part.pack(anchor="w", padx=20)
        ctk.CTkLabel(f, text="Part 1 and Part 2 each have half of every category x distance - for testing on two "
                             "days. Either way it carries on where the last session stopped.",
                     font=(FONT, 12), text_color=INK_600, wraplength=680, justify="left").pack(anchor="w", padx=20)
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
        try:
            trials_per_condition = int(values["trials_per_condition"])
        except ValueError:
            trials_per_condition = 0
        if not 1 <= trials_per_condition <= 50:
            self.setup_error.configure(text="Trials per category per distance must be a whole number from 1 to 50.")
            return
        part = self.part.get()
        self.begin.configure(state="disabled", text="Checking…")
        self.setup_error.configure(text="")

        def work():
            try:
                self.run.begin_session(values["enrolled_id"], values["enrolled_label"] or "Subject A",
                                       values["unknown_label"] or "Volunteer 1", self.medium.get(), timeout,
                                       trials_per_condition=trials_per_condition, part=part)
                self.window.after(0, self._show_trials)
            except (ValueError, requests.RequestException) as exc:
                message = str(exc)
                self.window.after(0, lambda: self._setup_failed(message))

        threading.Thread(target=work, daemon=True).start()

    def _show_folder_summary(self):
        self.folder_label.configure(text=self.run.folder_summary())

    def _new_test(self):
        from tkinter import messagebox

        if not messagebox.askyesno(
            "Start a new test?",
            "This moves the current results, trial plan and settings snapshots into an 'old-run-…' folder "
            "inside the results folder (nothing is deleted), so the next session starts a new test at "
            "trial 1.\n\nStart a new test?", parent=self.window,
        ):
            return
        moved = self.run.archive()
        self.setup_error.configure(text=f"Moved to {moved}" if moved else "There was nothing to move.",
                                   text_color=VERIFIED if moved else INK_600)
        self._show_folder_summary()

    def _setup_failed(self, message):
        self.setup_error.configure(text_color=DANGER)
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
        now = time.perf_counter()
        late_ms = (now - self._last_poll) * 1000 - POLL_MS if self._last_poll else 0
        self._last_poll = now
        try:
            # This window shares the gate monitor's screen loop: if this
            # comes late, the gate monitor's screen was frozen that long.
            if late_ms >= FREEZE_MS:
                self.run.note_freeze(late_ms)
                self._last_sample = now
            elif now - self._last_sample >= 1.0:
                self.run.sample_system()
                self._last_sample = now
            self.run.tick()
            self._refresh()
        finally:
            self.window.after(POLL_MS, self._poll)

    def _blur_limit(self):
        settings = (self.run.session or {}).get("settings") or {}
        return f"{settings['blur_min']:g}" if isinstance(settings.get("blur_min"), (int, float)) else "?"

    def _refresh(self):
        info = self.run.status()
        state = info["state"]
        if state == "setup":
            return
        trial = info["trial"]
        if trial:
            where = "" if info["part"] == "All" else f"{info['part']}  ·  "
            self.counter.configure(text=f"Trial {trial['trial']} of {info['plan_total']}  ·  {where}round "
                                        f"{trial['round']}  ·  {info['done']} of {info['total']} done")
            self.category.configure(text=trial["category"].upper(), text_color=CATEGORY_COLOR[trial["category"]])
            self.distance.configure(text=f"{trial['distance_m']:g} m")
        if state == "finished":
            self.counter.configure(text=f"{info['done']} of {info['total']} done")
            self.category.configure(text="ALL DONE", text_color=VERIFIED)
            self.distance.configure(text="")
            self.state_label.configure(
                text="Every trial in the plan has a result." if info["part"] == "All" else
                f"Every trial in {info['part']} has a result.", text_color=VERIFIED)
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
            if info.get("face_in_view"):
                text = ("NOT READY - the camera can see a face. The subject turns AWAY from the camera "
                        "(everyone else out of view); READY comes 4 s later.")
            elif info.get("card_wait"):
                text = f"Waiting for the card-tap request to expire… READY in {info.get('quiet_left', 0):.1f} s"
            else:
                text = f"No face in view - READY in {info.get('quiet_left', 0):.1f} s"
            self.state_label.configure(text=text, text_color=CAUTION)
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
                     f"\nsharpness {row['sharpness_median'] or '-'} (blur limit {self._blur_limit()}) · "
                     f"{row['frames_skipped_blur']} of {row['frames_with_face']} face frames skipped as blurry"
                     f" · real-face {row['liveness_score'] or '-'}"
                     f"\nspeed: answer {row['answer_ms_median'] or '-'} ms (backend {row['server_ms_median'] or '-'}, "
                     f"face AI {row['face_ai_ms_median'] or '-'}) · {row['screen_freezes']} screen freeze(s) · "
                     f"CPU up to {row['cpu_max_pct'] or '-'}% · memory up to {row['memory_max_pct'] or '-'}%"
            )
        else:
            self.result_label.configure(text="")
