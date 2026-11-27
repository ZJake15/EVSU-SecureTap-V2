import json
import os
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import requests

import camera_select
import lanes
from api_client import ApiClient
from camera import Camera, list_available_cameras
from card_readers import CardReaders
from config import data_file, load_config
from offline_queue import OfflineQueue
from monitor_stream import monitor_stream_loop
from ui import GateMonitorWindow, set_app_user_model_id

# The pause between face checks is config.scan_pause_seconds (0.2 s unless
# the launcher's speed mode says otherwise). The request itself takes the
# bulk of each cycle (image capture is instant off the continuous stream), so
# the pause is a small breather rather than the main throttle - the old 2s
# value meant someone walking past at normal speed could cross the frame
# between samples entirely.
STATUS_CHECK_INTERVAL_SECONDS = 5
# How often the gate tells the backend what its camera runs at, when nothing
# changed (Settings > Camera shows it - see camera_report).
CAMERA_REPORT_SECONDS = 60
# While a gate has no exit camera: how often to look for a newly plugged-in
# one to suggest (main's watch_for_exit_camera).
EXIT_SUGGEST_SECONDS = 30

# Per camera (lanes.ENTRY / lanes.EXIT): whether the backend's last answer
# said its face check itself failed (/api/identify's face_check_error - the
# face model or the photo broke, not "nobody here"). Set by scan_loop, read by
# status_worker, which turns the backend light red with "Face check error".
_face_check_failing = {}

# Read by the root launcher (launcher.py's _read_last_session_summary) to show
# a "last session" line before the entry-agent is even started again - in
# the data folder (config.data_file), whatever the working directory.
LAST_SESSION_PATH = Path(data_file("last_session.json"))

# One gate monitor per computer. Two would fight over the camera, and each
# one opening ends the shift of whoever is on duty at its gate - so with both
# the Admin launcher and the Gate icon able to open it, the second one says
# so and quits (launcher.py matches the marker).
GATE_MONITOR_MUTEX = "EVSU.SecureTap.GateMonitor"
ALREADY_OPEN_MARKER = "SECURETAP_GATE_MONITOR_ALREADY_OPEN"
_instance_lock = None  # held for the life of this process


def _claim_single_instance():
    """True unless another gate monitor already runs on this computer (a
    Windows named mutex, released by Windows when this process ends)."""
    global _instance_lock
    if os.name != "nt":
        return True
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    _instance_lock = kernel32.CreateMutexW(None, False, GATE_MONITOR_MUTEX)
    return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS


def _say_already_open():
    print(ALREADY_OPEN_MARKER, flush=True)
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo("EVSU SecureTap", "The gate monitor is already open on this computer - look for it on "
                                              "the taskbar.", parent=root)
        root.destroy()
    except Exception:
        pass  # no display to show it on - the marker is enough


def _write_last_session_summary(config, monitor):
    """Best-effort - a guard closing the gate monitor should never see an
    error dialog because a convenience file on disk couldn't be written."""
    try:
        LAST_SESSION_PATH.write_text(
            json.dumps(
                {
                    "ended_at": datetime.now(timezone.utc).isoformat(),
                    "gate_location": config.gate_location,
                    "direction": config.direction,
                    "entries": monitor.stats.get("entries", 0),
                    "exits": monitor.stats.get("exits", 0),
                    "unknown": monitor.stats.get("unknown", 0),
                    "spoof": monitor.stats.get("spoof", 0),
                    "occlusion": monitor.stats.get("occlusion", 0),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


# The gate monitor's screen layout (ui.GateMonitorWindow.LAYOUTS, layouts.py)
# and, for Entrance + Exit, side by side or big + small - picked from the
# Layout menu in its status bar, remembered per computer in the data folder.
# A file from before the Layout menu holds an old name; the window maps it.
LAYOUT_PATH = Path(data_file("gate_layout.json"))


def _load_layout():
    """(layout, dual mode) - ("split", "side") when nothing's saved."""
    try:
        saved = json.loads(LAYOUT_PATH.read_text(encoding="utf-8"))
        return saved.get("layout") or "split", saved.get("dual") or "side"
    except (OSError, ValueError, AttributeError):
        return "split", "side"


def _save_layout(layout, dual="side"):
    try:
        LAYOUT_PATH.write_text(json.dumps({"layout": layout, "dual": dual}), encoding="utf-8")
    except OSError:
        pass  # a convenience - picking it again next time still works


# How loud the alert sounds are (the speaker button in the gate monitor's
# status bar - alert_sounds.py), remembered per computer in the data folder. A
# mute isn't remembered: it ends by itself, and a restart ends it too.
SOUND_PATH = Path(data_file("sound_settings.json"))


def _load_sound_volume():
    try:
        volume = float(json.loads(SOUND_PATH.read_text(encoding="utf-8")).get("volume", 1.0))
        return min(1.0, max(0.0, volume))
    except (OSError, ValueError, AttributeError, TypeError):
        return 1.0


def _save_sound_volume(volume):
    try:
        SOUND_PATH.write_text(json.dumps({"volume": round(float(volume), 2)}), encoding="utf-8")
    except OSError:
        pass  # a convenience - setting it again next time still works


def _extract_image_size(response, fallback):
    """The backend returns image_size as {"width": W, "height": H} (JSON has
    no tuple type), but the UI wants a plain (width, height) pair - falls
    back to the size camera.capture_jpeg() reported if the key's missing."""
    size = response.get("image_size")
    if isinstance(size, dict) and "width" in size and "height" in size:
        return size["width"], size["height"]
    return fallback


PHOTO_CACHE_SIZE = 200  # photos kept for the live log - the oldest goes first


def _fetch_photo_cached(api_client, photo_cache, url):
    """Fetches a reference/captured photo's bytes, cached by URL so a
    person (or lingering unrecognized face, which server-side dedup already
    keys to the same captured_photo URL across frames) is only fetched once
    per camera-scanner session, not once per ~0.2s poll cycle.

    Cached by the file's address without its ?token=: the token is signed
    with the time, so the same photo's URL changes every second - keyed by
    the whole URL, a matched face's photo was fetched again (on the scan
    thread) every second and kept each time."""
    if not url:
        return None
    key = url.split("?", 1)[0]
    if key in photo_cache:
        return photo_cache[key]
    try:
        photo_bytes = api_client.fetch_photo(url)
    except requests.RequestException:
        photo_bytes = None
    if len(photo_cache) >= PHOTO_CACHE_SIZE:
        photo_cache.pop(next(iter(photo_cache)))
    photo_cache[key] = photo_bytes
    return photo_bytes


def _build_recognition(direction, result, api_client, photo_cache):
    """Turns one /identify result item into what the camera scanner window
    needs to draw a labeled box: a similarity-derived confidence percentage
    (only meaningful for an actual match), a tiebreak flag for the "please
    tap your card" case, and the direction, since a single camera has one
    fixed direction for this whole deployment rather than a per-person
    toggle. Also carries the photo the guard should see in the live log -
    the enrolled reference photo for a match, or the actual cropped capture
    for an unrecognized face, so "who does the system think this is" is
    never just a name."""
    box = result.get("box")
    if box is None:
        return None  # the "no face"/"nothing enrolled" transient cases

    success = bool(result.get("success"))
    # ArcFace match strength is a cosine SIMILARITY (higher = better match),
    # the opposite sense of the old dlib pipeline's "distance" (lower =
    # better) - confidence is a direct percentage of it, not 1-minus.
    similarity = result.get("similarity")
    confidence = None
    if success and similarity is not None:
        confidence = max(0, round(similarity * 100))

    tiebreak = bool(result.get("tiebreak_required"))
    # A tiebreak forced by a known confusable pair (see ConfusablePair on the
    # backend) - the face match itself was fine, it was overridden anyway
    # because the matched person is on record as easily confused with
    # someone similar. Distinct from an ordinary ambiguous-match tiebreak so
    # the box label can say so, not just "please tap your card" generically.
    confusable_pair = bool(result.get("confusable_pair"))
    # A manual backstop for a confusable pair (a visible mole/scar/glasses) -
    # never used by matching itself, just something for the guard to check
    # by eye while waiting for the tap.
    distinguishing_notes = result.get("distinguishing_notes") or []
    # A frame skipped for being too blurry/edge-cropped, or an unmatched
    # face that hasn't yet accumulated enough agreement to count as a real
    # "Unknown" - see IdentifyView._skip_reason/_confirm_or_vote_unmatched.
    # Neither is a decided outcome, so the UI shows "Checking..." instead of
    # flashing red on a single bad frame.
    retry = bool(result.get("retry"))
    # A short, actionable label for the box while a face is being skipped
    # ("Face the camera", "Move into view", "Hold steady"). Only the quality
    # skips set it; the voting paths don't, and fall back to "Checking...".
    hint = result.get("hint")
    # A face that failed the liveness (anti-spoofing) check - see
    # IdentifyView._confirm_or_vote_spoof. Checked before matching, so this
    # is never also `success`; kept as its own flag rather than folded into
    # "Unknown" since it's a security event, not a recognition miss.
    spoof_suspected = bool(result.get("spoof_suspected"))
    # occlusion_suspected: THIS frame's own outcome is occlusion - same role
    # spoof_suspected plays for a suspected spoof. occlusion_detected is a
    # separate, broader note that can also be True on an otherwise matched/
    # unmatched/spoof result (see EntryLog.occlusion_detected) - a moment of
    # occlusion seen earlier in the same encounter that ISN'T this frame's
    # own outcome. Kept as two flags rather than one so "is this the outcome"
    # and "did this happen at some point" can never be confused with each
    # other the way a single shared flag would risk.
    occlusion_suspected = bool(result.get("occlusion_suspected"))
    occlusion_seen = bool(result.get("occlusion_detected"))
    # Recognized, but refused: not on this semester's enrolled list (Settings >
    # Semester Enrollment: "Refuse entry") - still their name and photo.
    refused_not_enrolled = result.get("refused") == "not_enrolled"
    photo_url = result.get("person_photo") if success or refused_not_enrolled else result.get("captured_photo")
    return {
        "box": box,
        "matched": success,
        "tiebreak": tiebreak,
        "confusable_pair": confusable_pair,
        "distinguishing_notes": distinguishing_notes,
        "retry": retry,
        "hint": hint,
        "spoof_suspected": spoof_suspected,
        "occlusion_suspected": occlusion_suspected,
        "occlusion_seen": occlusion_seen,
        "refused_not_enrolled": refused_not_enrolled,
        "name": (
            result.get("person_name")
            if success or refused_not_enrolled
            else "Please uncover your face" if occlusion_suspected
            else "Possible spoof" if spoof_suspected
            else "Unknown"
        ),
        "candidate_names": result.get("candidate_names") or [],
        "student_id": result.get("student_or_employee_id"),
        "department": result.get("department_or_course"),
        "distinguishing_note": result.get("distinguishing_note"),
        "photos_due": bool(result.get("photos_due")),
        # A student not on the current semester's enrolled list - let in, flagged.
        "not_enrolled": bool(result.get("not_enrolled")),
        # Unknown entries this recognition undid (the same face, moments
        # ago - see IdentifyView._correct_recent_unknowns): the live log
        # takes those rows out again.
        "corrected_log_ids": result.get("corrected_log_ids") or [],
        "confidence": confidence,
        "log_id": result.get("log_id"),
        "deduped": bool(result.get("deduped")),
        # Settings page: "Alert on repeated unknown faces" - the backend sets
        # this when the same unrecognized face keeps coming back.
        "repeated_unknown": bool(result.get("repeated_unknown")),
        "repeated_unknown_count": result.get("repeated_unknown_count"),
        "direction": direction,
        # Only sent with "Show distance on face boxes" on (Settings, Advanced).
        "distance_m": result.get("distance_m"),
        "photo_bytes": _fetch_photo_cached(api_client, photo_cache, photo_url),
        # An odd entry/exit pattern (backend EntryLog.PatternFlag) - the live
        # log tags the row so the guard can ask.
        "pattern_flag": result.get("pattern_flag") or "",
        # STUDENT/STAFF, school or office, department, role - as Settings >
        # Live Log Details has them on (backend users/live_details.py).
        "details": result.get("person_details") or {},
    }


def _build_profile(api_client, result, card_id, direction):
    """Turns one /verify result into the profile dict the gate monitor's card
    panel expects, fetching the actual photo bytes from the URL the backend
    returned. `direction` rides along so the tap's live-log badge can say
    which way through the gate it was, the same as a face event's."""
    photo_bytes = None
    photo_url = result.get("person_photo")
    if photo_url:
        try:
            photo_bytes = api_client.fetch_photo(photo_url)
        except requests.RequestException:
            photo_bytes = None
    return {
        "name": result.get("person_name"),
        "photo_bytes": photo_bytes,
        "role": result.get("role"),
        "student_id": result.get("student_or_employee_id"),
        "department": result.get("department_or_course"),
        "card_id": card_id,
        "direction": direction,
        # A manual backstop for a confusable pair - present whenever this tap
        # confirmed someone flagged that way, whether or not it happened to
        # be resolving a forced tiebreak (see person_payload on the backend).
        "distinguishing_note": result.get("distinguishing_note"),
        "photos_due": bool(result.get("photos_due")),
        "not_enrolled": bool(result.get("not_enrolled")),
        "pattern_flag": result.get("pattern_flag") or "",
        "details": result.get("person_details") or {},
    }


def live_settings(config):
    """What this gate monitor runs with right now: the launcher's speed mode
    (config.py) until Settings > Camera says otherwise - apply_camera_settings
    keeps it current. Shared with the test mode's settings snapshot."""
    return {
        "scan_pause_seconds": config.scan_pause_seconds, "upload_max_dimension": config.upload_max_dimension,
        "video_fps": config.video_fps, "student_display_fps": config.student_display_fps, "camera_name": "",
    }


def apply_camera_settings(settings, config, camera, ui, live):
    """Settings > Camera, as sent with a scan answer. "auto" keeps this
    computer's speed mode; the camera reconnects only when its picture
    settings changed (Camera.configure)."""
    def chosen(key, fallback, cast):
        value = settings.get(key)
        if value in (None, "", "auto"):
            return fallback
        try:
            return cast(value)
        except (TypeError, ValueError):
            return fallback

    live["scan_pause_seconds"] = chosen("scan_pause", config.scan_pause_seconds, float)
    live["upload_max_dimension"] = chosen("upload_size", config.upload_max_dimension, int)
    camera.max_upload_dimension = live["upload_max_dimension"]
    live["video_fps"] = chosen("video_fps", config.video_fps, int)
    ui.set_video_fps(live["video_fps"])
    live["student_display_fps"] = chosen("student_display_fps", config.student_display_fps, int)
    ui.set_student_display_fps(live["student_display_fps"])
    camera.configure(settings)


def camera_report(camera, live):
    """What this gate's camera and scan actually run at - for Settings > Camera."""
    return {
        **camera.report(), "camera": live.get("camera_name") or None, "scan_pause": live["scan_pause_seconds"],
        "upload_size": live["upload_max_dimension"], "video_fps": live["video_fps"],
        "student_display_fps": live["student_display_fps"],
    }


def _report_camera(lane_name, api_client, camera, live, last):
    """Sends camera_report when it changes (or once its frame rate is first
    measured), and otherwise once every CAMERA_REPORT_SECONDS - on its own
    thread, so it never delays a face check. Under the camera's lane name
    (lanes.py), so Settings > Camera lists the exit camera on its own row.
    The entrance camera's report also carries the gate monitor's own state
    for the dashboard's System Status page (live["health"]: its version,
    card taps waiting in the offline queue, and the last card tap)."""
    report = {**camera_report(camera, live), **live.get("health", {})}
    if report["resolution"] is None:
        return  # no picture yet
    steady = {**report, "fps": report["fps"] is not None}
    now = time.monotonic()
    if steady == last.get("steady") and now - last.get("at", 0.0) < CAMERA_REPORT_SECONDS:
        return
    last["steady"], last["at"] = steady, now

    def send():
        try:
            api_client.camera_report(lane_name, report)
        except requests.RequestException:
            pass  # sent again later

    threading.Thread(target=send, daemon=True).start()


def scan_loop(config, api_client, camera, ui, stop_event, test_run=None, live=None, direction=lanes.ENTRY):
    """Runs continuously on its own thread: samples a frame and checks every
    face in it against every enrolled face (1:N search per face) - this is
    the primary gate check, no card tap required, and handles several
    people walking through together in the same frame. Kept off the Tk
    thread so a burst of faces during class change can't freeze the UI.

    One loop per camera: `direction` says which - the entrance camera's
    (config.direction, always entry) or the exit camera's (lanes.py), each
    with its own Camera and its own ApiClient connection.

    test_run (the capstone test mode, test_mode.py) only watches: it gets
    each frame's capture and answer times and the answer, and the frame is
    marked as a test frame for the backend - nothing else changes. The
    entrance camera only."""
    # The backend sends its current Settings-page values with every answer,
    # so a change made on the dashboard reaches this gate monitor within a
    # frame or two - no restart. Only passed on to the UI when they change.
    last_threshold = None
    last_alerts = None
    last_sign_in = None
    last_camera = None
    last_report = {}
    live = live if live is not None else live_settings(config)
    photo_cache = {}  # URL -> bytes, lives for this scan session
    while not stop_event.is_set():
        t_frame = time.perf_counter()
        try:
            image_bytes, image_size = camera.capture_jpeg()
        except RuntimeError:
            stop_event.wait(live["scan_pause_seconds"])
            continue
        t_sent = time.perf_counter()

        try:
            response = api_client.identify(
                config.gate_location, direction, image_bytes, camera_zoom=camera.zoom,
                test_mode=test_run is not None,
            )
            ui.show_offline(False)
            _face_check_failing[direction] = bool(response.get("face_check_error"))
            threshold = response.get("threshold")
            if threshold is not None and threshold != last_threshold:
                ui.set_threshold(threshold)
                last_threshold = threshold
            alerts = response.get("alerts")
            if alerts is not None and alerts != last_alerts:
                ui.set_alert_settings(alerts)
                last_alerts = alerts
            # Who's on duty - a sign-out from elsewhere, or a shift reaching
            # its time limit, shows up here within a frame or two.
            sign_in = response.get("gate_sign_in")
            if sign_in is not None and sign_in != last_sign_in:
                ui.set_gate_sign_in(sign_in)
                last_sign_in = sign_in
            # Settings > Scheduling > Gate opening hours.
            ui.set_after_hours(response.get("after_hours", False))
            # Settings > Camera - resolution, frame rate, face-check pace...
            camera_settings = response.get("camera")
            if camera_settings is not None and camera_settings != last_camera:
                apply_camera_settings(camera_settings, config, camera, ui, live)
                last_camera = camera_settings
            recognitions = [
                r
                for r in (
                    _build_recognition(direction, item, api_client, photo_cache)
                    for item in response.get("results", [])
                )
                if r is not None
            ]
            if test_run is not None:
                # The answer is ready to draw - the guard sees it on the next
                # screen refresh (~66 ms).
                test_run.on_frame(t_frame, time.perf_counter(), response, image_size, camera.capture_size(),
                                  t_sent=t_sent)
            ui.show_recognitions(recognitions, _extract_image_size(response, image_size), direction)
        except requests.RequestException:
            # No offline queueing here - a stale queued frame from a live scan
            # has no value once connectivity returns; just keep sampling.
            ui.show_offline(True)
        except Exception:
            # Anything else (an unexpected response shape, a decoding error)
            # is logged and skipped - letting it escape would end this thread,
            # and the gate would silently stop recognizing anyone while the
            # feed kept looking live.
            traceback.print_exc()

        _report_camera(lanes.lane(config.gate_location, direction), api_client, camera, live, last_report)
        stop_event.wait(live["scan_pause_seconds"])


def handle_tap(config, api_client, offline_queue, ui, nfc_id, direction=lanes.ENTRY):
    """Runs off the Tk main thread. A card tap is a lookup that shows the
    guard the person's details for a cross-check - the actual entry decision
    comes from the continuous scan, not this. Either way the outcome lands
    in the monitor's live log, so a tap is as visible as a face event.
    `direction`: which side's reader it was tapped on (card_readers.py)."""
    ui.show_card_status("Checking card...")
    try:
        result = api_client.verify(config.gate_location, direction, nfc_id=nfc_id)
    except requests.exceptions.HTTPError as exc:
        ui.show_card_failure(_describe_http_error(exc.response), "server_error")
        return
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        offline_queue.enqueue(nfc_id, config.gate_location, direction)
        ui.show_card_failure("Offline - tap queued, will sync automatically.", "offline")
        return
    except requests.RequestException:
        # Reached the server but got something unusable back (e.g. a non-JSON
        # error page) - not worth queueing, but the guard must see that the
        # tap didn't go through rather than nothing happening at all.
        ui.show_card_failure("The server sent an unexpected reply - tap again.", "server_error")
        return

    try:
        if result.get("staff_card"):
            # A guard's own staff ID card: it signs them in for duty instead
            # of being looked up as a student (Settings -> "Guards sign in at
            # the gate monitor").
            if result.get("success"):
                status = result.get("gate_sign_in") or {}
                ui.set_gate_sign_in(status)
                ui.show_staff_signed_in((status.get("on_duty") or {}).get("name") or "Guard")
            elif result.get("face_needed"):
                # Settings: "Card, then face" - the sign-in window now asks
                # for the card owner's face (face_sign_in_loop).
                ui.show_card_status("")
                ui.ask_for_face(result.get("name") or "Guard", result.get("pending"))
            else:
                ui.show_card_failure(result.get("reason") or "Staff ID card.", result.get("reason_code"))
            return
        if not result.get("success"):
            ui.show_card_failure(result.get("reason") or "Not enrolled.", result.get("reason_code"))
            return
        ui.show_card_match(
            _build_profile(api_client, result, card_id=nfc_id, direction=direction)
        )
    except Exception:
        traceback.print_exc()
        ui.show_card_failure("Something went wrong reading this tap - tap again.", "server_error")


def handle_sign_in(config, api_client, ui, username, password):
    """A guard signing in for duty with their password - off the Tk thread."""
    try:
        status = api_client.gate_sign_in(config.gate_location, username, password)
    except requests.exceptions.HTTPError as exc:
        detail = None
        try:
            detail = exc.response.json().get("detail")
        except (AttributeError, ValueError):
            pass
        ui.show_sign_in_result(False, detail or _describe_http_error(exc.response))
        return
    except requests.RequestException:
        ui.show_sign_in_result(False, "Can't reach the backend - try again in a moment.")
        return
    ui.set_gate_sign_in(status)
    ui.show_sign_in_result(True, None)


FACE_SIGN_IN_PACE_SECONDS = 0.7
FACE_SIGN_IN_IDLE_SECONDS = 0.3  # how often to look again while nobody's in view


def face_sign_in_loop(config, api_client, camera, ui, stop, pending=None):
    """The sign-in window's face scan (Settings -> "Sign in with face
    scan"), off the Tk thread: a frame from the entrance camera goes to the
    backend about every FACE_SIGN_IN_PACE_SECONDS until it signs the guard
    in, says to stop, or the window closes (stop is set)."""
    told_no_face = False
    while not stop.is_set():
        # Only while the entrance camera actually sees a face (the gate's
        # own scan keeps running behind the sign-in window and knows). The
        # window opens by itself whenever nobody is on duty, so sending every
        # frame regardless kept the backend's face AI busy with an empty
        # doorway - the same AI that checks everyone coming through the gate.
        if not ui.face_in_view():
            if not told_no_face:
                ui.show_face_sign_in({"reason": "Look at the camera.", "reason_code": "no_face"})
                told_no_face = True
            stop.wait(FACE_SIGN_IN_IDLE_SECONDS)
            continue
        told_no_face = False
        try:
            image_bytes, _size = camera.capture_jpeg()
        except RuntimeError:
            ui.show_face_sign_in({"reason": "Waiting for the camera…", "reason_code": "no_camera"})
            stop.wait(1.0)
            continue
        try:
            result = api_client.gate_face_sign_in(config.gate_location, image_bytes, pending)
        except requests.exceptions.HTTPError as exc:
            result = {"reason": _describe_http_error(exc.response), "reason_code": "server_error"}
        except requests.RequestException:
            result = {"reason": "Can't reach the backend - still trying…", "reason_code": "offline"}
        except Exception:
            traceback.print_exc()
            result = {"reason": "Something went wrong - still trying…", "reason_code": "server_error"}
        if stop.is_set():
            return
        if result.get("success"):
            status = result.get("gate_sign_in") or {}
            ui.set_gate_sign_in(status)
            ui.show_staff_signed_in(result.get("name") or (status.get("on_duty") or {}).get("name") or "Guard")
            return
        ui.show_face_sign_in(result)
        if result.get("reason_code") in ("card_first", "face_off"):
            return  # a new card tap (or the setting back on) starts it again
        stop.wait(FACE_SIGN_IN_PACE_SECONDS)


def handle_sign_out(config, api_client, ui, shift_id):
    try:
        ui.set_gate_sign_in(api_client.gate_sign_out(config.gate_location, shift_id=shift_id))
    except requests.RequestException:
        traceback.print_exc()


def _refusal_text(response):
    """The backend's own reason for refusing (e.g. "Sign in for duty first")
    - only a refusal of the service token itself reads as a token problem."""
    try:
        detail = response.json().get("detail")
    except (AttributeError, ValueError):
        detail = None
    if detail and not str(detail).startswith(("You do not have permission", "Authentication credentials")):
        return str(detail)
    return _describe_http_error(response)


def _describe_http_error(response):
    if response is None:
        return "Server error."
    if response.status_code in (401, 403):
        return "Rejected: check SERVICE_TOKEN in entry-agent/.env matches the backend."
    try:
        data = response.json()
        return data.get("detail") or data.get("reason") or f"Server error ({response.status_code})."
    except ValueError:
        return f"Server error ({response.status_code})."


def main():
    # Distinct from the launcher's own id (see launcher.py) - each process
    # needs its own so Windows' taskbar treats them as separate apps with
    # separate icons, rather than grouping both under plain python.exe's.
    set_app_user_model_id("EVSU.SecureTap.EntryAgent")
    if not _claim_single_instance():
        _say_already_open()
        return
    config = load_config()
    if not config.service_token:
        print(
            "WARNING: SERVICE_TOKEN is not set in .env - the backend will reject requests.",
            file=sys.stderr,
        )

    api_client = ApiClient(config.api_base_url, config.service_token)

    # Which camera: a plugged-in one before the laptop's built-in one, the
    # one someone picked before (remembered by name) first among equals -
    # see camera_select.py. CAMERA_INDEX only matters if none can be listed.
    # Best-effort - a machine with zero cameras (or a pygrabber hiccup) just
    # means an empty list, which the dropdown renders as "No camera found".
    try:
        cameras = camera_select.classify(list_available_cameras())
    except Exception as exc:
        print(f"WARNING: could not enumerate cameras ({exc})", file=sys.stderr)
        cameras = []
    camera_index = camera_select.choose(cameras, camera_select.remembered_name(), fallback_index=config.camera_index,
                                        place=camera_select.remembered_place())
    camera_names = {index: name for index, name, _kind in cameras}
    camera_options = [(index, f"{name} ({camera_select.LABELS[kind]})") for index, name, kind in cameras]
    print(f"camera: using {camera_names.get(camera_index, f'#{camera_index}')}", file=sys.stderr)

    camera = Camera(
        camera_index, exposure=config.camera_exposure, max_upload_dimension=config.upload_max_dimension,
        zoom=config.camera_zoom,
    )
    # What this gate runs with right now (speed mode, then Settings > Camera).
    live = live_settings(config)
    live["camera_name"] = camera_names.get(camera_index, f"#{camera_index}")
    # For the dashboard's System Status page, sent with the entrance camera's
    # report (_report_camera): the queue count is kept current by
    # status_worker, the last tap by tap_from_reader.
    live["health"] = {"app_version": config.app_version, "queue": 0}

    def pick_camera(index):
        """The guard chose the entrance camera in the gate monitor - use it
        now and prefer it next time."""
        camera.set_index(index)
        live["camera_name"] = camera_names.get(index, f"#{index}")
        if index in camera_names:
            camera_select.remember(camera_names[index], place=camera_select.place_of(cameras, index))

    # The exit camera (lanes.py): only once one has been chosen in the gate
    # monitor's "Cameras & card readers" window - remembered by name, like
    # the entrance camera. Without one this gate records entries only, as
    # before. It gets its own Camera, scan loop, backend connection and
    # Cameras-page stream, all stopped together by its own stop event.
    exit_lane = {}

    def start_exit_camera(index):
        exit_camera = Camera(index, exposure=config.camera_exposure,
                             max_upload_dimension=config.upload_max_dimension, zoom=config.camera_zoom)
        exit_live = live_settings(config)
        exit_live["camera_name"] = camera_names.get(index, f"#{index}")
        exit_stop = threading.Event()
        exit_api = ApiClient(config.api_base_url, config.service_token)  # its own connection - never waits behind the entrance's
        try:
            exit_camera.start()
        except Exception as exc:
            print(f"WARNING: exit camera unavailable ({exc})", file=sys.stderr)
        exit_lane.update(camera=exit_camera, stop=exit_stop, index=index)
        threading.Thread(target=scan_loop, args=(config, exit_api, exit_camera, monitor, exit_stop, None, exit_live,
                                                 lanes.EXIT), daemon=True).start()
        threading.Thread(target=monitor_stream_loop, args=(lanes.lane(config.gate_location, lanes.EXIT), exit_api,
                                                           exit_camera, exit_stop), daemon=True).start()
        monitor.show_exit_feed(exit_camera.get_preview_frame, exit_camera.capture_size, exit_camera.measured_fps,
                               config.camera_zoom, index)
        print(f"exit camera: using {exit_live['camera_name']}", file=sys.stderr)

    def stop_exit_camera():
        if exit_lane:
            exit_lane["stop"].set()
            exit_lane["camera"].stop()
            exit_lane.clear()

    def refresh_cameras(done):
        """The "Cameras & card readers" window is opening: the camera list
        again, so a camera plugged in since the gate monitor started can be
        picked without restarting it. Names only (Windows' device list,
        max_probe=0): no camera is ever opened, so the ones in use keep
        streaming. Asking Windows can take seconds the first time, so it's
        done on a thread of its own; done(result) then runs on the Tk thread
        (apply_camera_list)."""
        def work():
            try:
                import ctypes

                ctypes.windll.ole32.CoInitializeEx(None, 0x2)  # this thread asks through COM too (pygrabber)
            except Exception:
                pass
            try:
                fresh = camera_select.classify(list_available_cameras(max_probe=0))
            except Exception:
                traceback.print_exc()
                fresh = []
            monitor.run_on_tk(lambda: done(apply_camera_list(fresh)))

        threading.Thread(target=work, daemon=True).start()

    def apply_camera_list(fresh):
        """A fresh camera list, on the Tk thread. A camera plugged in can
        renumber the others, so the ones in use are found again by name and
        their number corrected - the number is only used when a camera
        reconnects. Returns (options, entrance index, exit index), or None to
        keep the list as it was."""
        if not fresh:
            return None  # couldn't list them - better the old list than none
        moved = camera_select.same_device(cameras, camera.index, fresh)
        if moved is not None:
            camera.index = moved
        if exit_lane:
            moved = camera_select.same_device(cameras, exit_lane["camera"].index, fresh)
            if moved is not None:
                exit_lane["camera"].index = exit_lane["index"] = moved
        cameras[:] = fresh
        camera_names.clear()
        camera_names.update({index: name for index, name, _kind in fresh})
        options = [(index, f"{name} ({camera_select.LABELS[kind]})") for index, name, kind in fresh]
        return options, camera.index, (exit_lane["camera"].index if exit_lane else None)

    def pick_exit_camera(index):
        """The guard chose the exit camera (or None: no exit camera) - Tk thread."""
        stop_exit_camera()
        monitor.hide_exit_feed()
        if index is None:
            camera_select.remember(None, lanes.EXIT)
            return
        start_exit_camera(index)
        if index in camera_names:
            camera_select.remember(camera_names[index], lanes.EXIT, place=camera_select.place_of(cameras, index))

    # "Use it as the exit camera?" (gate_tools.ExitCameraQuestion): while this
    # gate has no exit camera, the camera list is looked at every
    # EXIT_SUGGEST_SECONDS; a plugged-in camera besides the entrance one gets
    # the question - once per camera per session ("Not now"), or never again
    # ("Don't ask again", kept in camera_choice.json).
    suggestion = {"asking": False, "declined": set(), "names": tuple(name for _i, name, _k in cameras)}

    def suggest_exit_camera(fresh=None):
        """Tk thread. fresh: a new classify() list, or None to use the one
        the gate monitor started with."""
        if monitor.is_closed() or exit_lane or suggestion["asking"] or camera_select.exit_suggestion_off():
            return
        if fresh:
            listed = apply_camera_list(fresh)
            if listed:
                monitor.set_camera_list(listed)
        index = camera_select.exit_candidate(cameras, camera.index)
        if index is None:
            return
        key = (camera_names.get(index), camera_select.place_of(cameras, index))
        if key in suggestion["declined"]:
            return
        suggestion["asking"] = True

        def answer(choice):
            suggestion["asking"] = False
            if choice == "use" and not exit_lane:
                now = next((i for i, name, _k in cameras if (name, camera_select.place_of(cameras, i)) == key), None)
                if now is not None and now != camera.index:
                    pick_exit_camera(now)
            elif choice == "later":
                suggestion["declined"].add(key)
            elif choice == "never":
                camera_select.stop_suggesting_exit()

        monitor.ask_exit_camera(camera_names.get(index, f"Camera {index}"), answer)

    def watch_for_exit_camera():
        if monitor.is_closed():
            return
        monitor.window.after(EXIT_SUGGEST_SECONDS * 1000, watch_for_exit_camera)
        if exit_lane or suggestion["asking"] or camera_select.exit_suggestion_off():
            return

        def work():
            try:
                import ctypes

                ctypes.windll.ole32.CoInitializeEx(None, 0x2)  # pygrabber asks Windows through COM
            except Exception:
                pass
            try:
                names = list_available_cameras(max_probe=0)
            except Exception:
                return
            key = tuple(name for _i, name in names)
            if not names or key == suggestion["names"]:
                return  # nothing plugged in or out - no need to ask Windows for details again
            suggestion["names"] = key
            try:
                fresh = camera_select.classify(names)
            except Exception:
                traceback.print_exc()
                return
            monitor.run_on_tk(lambda: suggest_exit_camera(fresh))

        threading.Thread(target=work, daemon=True).start()

    # Which reader a card was tapped on (card_readers.py): the one set as the
    # exit's makes it an exit tap; any other is the entrance's.
    readers = CardReaders()
    readers.start()

    def reader_setup(action):
        """The "Cameras & card readers" window's reader buttons - Tk thread."""
        if action == "set_exit":
            readers.awaiting_exit_tap = True
        elif action == "clear":
            readers.clear_exit_reader()
        else:  # "cancel" - the window closed while waiting for a tap
            readers.awaiting_exit_tap = False
        monitor.set_reader_status(readers.status())
    # Starts a background thread that opens the webcam (retrying on its own
    # timer for as long as the app runs if none is connected yet, or it's
    # unplugged mid-session - see Camera._run_loop) - never blocks here and
    # never raises even if no camera ever connects. This broad except is
    # just a second line of defense against something unexpected in
    # starting that thread itself, so a surprise can't take the whole
    # entry-agent down with it. Either way the gate monitor still opens;
    # its video panel shows "No camera connected" and NFC taps/manual
    # overrides keep working.
    try:
        camera.start()
    except Exception as exc:
        print(f"WARNING: camera unavailable, continuing without one ({exc})", file=sys.stderr)

    offline_queue = OfflineQueue(config.offline_db_path, api_client)
    offline_queue.start_background_sync()

    # No menu in front of this. Choosing "Entry Agent" in the system launcher
    # is already the decision; opening a second screen to ask again would just
    # be a click everyone makes every time. The monitor is the app.
    stop_event = threading.Event()

    def tap_from_reader(nfc_id):
        live["health"]["last_tap"] = datetime.now().astimezone().isoformat(timespec="seconds")
        # A moment for the raw input listener to note the last keys of this
        # tap - it runs on its own thread, beside the window's.
        time.sleep(0.05)
        if readers.awaiting_exit_tap:
            # "Set exit reader": this tap only says which reader is the exit's.
            device = readers.assign_exit_from_last_tap()
            message = "Exit reader set." if device else "Couldn't tell which reader that was - try again."
            monitor.set_reader_status({**readers.status(), "message": message})
            return
        handle_tap(config, api_client, offline_queue, monitor, nfc_id, readers.direction_of_last_tap())

    def on_tap(nfc_id):
        threading.Thread(target=tap_from_reader, args=(nfc_id,), daemon=True).start()

    def on_sign_in(username, password):
        threading.Thread(
            target=handle_sign_in, args=(config, api_client, monitor, username, password), daemon=True
        ).start()

    def on_sign_out(shift_id):
        threading.Thread(target=handle_sign_out, args=(config, api_client, monitor, shift_id), daemon=True).start()

    # The sign-in window's face scan - one at a time, on its own connection
    # so it never waits behind the scan loop's frames.
    face_scan = {"stop": None}
    face_api = ApiClient(config.api_base_url, config.service_token)

    def on_face_scan(start, pending=None):
        if face_scan["stop"] is not None:
            face_scan["stop"].set()
            face_scan["stop"] = None
        if start:
            stop = threading.Event()
            face_scan["stop"] = stop
            threading.Thread(target=face_sign_in_loop, args=(config, face_api, camera, monitor, stop, pending),
                             daemon=True).start()

    # Find person (gate_tools.FindPersonWindow): its own connection, so a
    # search never waits behind the scan loop's frames. Both answers go back
    # to the window on the Tk thread.
    find_api = ApiClient(config.api_base_url, config.service_token)
    find_photos = {}

    def find_person(query, done):
        def work():
            try:
                results = find_api.find_person(config.gate_location, query).get("results") or []
                for person in results:
                    person["photo_bytes"] = _fetch_photo_cached(find_api, find_photos, person.get("person_photo"))
                answer = {"results": results}
            except requests.exceptions.HTTPError as exc:
                answer = {"error": _refusal_text(exc.response)}
            except requests.RequestException:
                answer = {"error": "Can't reach the backend - try again in a moment."}
            monitor.run_on_tk(lambda: done(answer))

        threading.Thread(target=work, daemon=True).start()

    def let_in(person, direction, reason, done):
        """Let in (or out) by hand - recorded under the guard on duty; the
        live log shows it like an accepted card, marked BY HAND."""
        def work():
            try:
                result = find_api.manual_entry(config.gate_location, person.get("student_or_employee_id"),
                                               direction, reason)
            except requests.exceptions.HTTPError as exc:
                message = _refusal_text(exc.response)
                monitor.run_on_tk(lambda: done({"error": message}))
                return
            except requests.RequestException:
                monitor.run_on_tk(lambda: done({"error": "Can't reach the backend - nothing was saved. Try again."}))
                return
            profile = _build_profile(find_api, result, card_id=None, direction=direction)
            if not profile["photo_bytes"]:
                profile["photo_bytes"] = person.get("photo_bytes")
            profile["manual_reason"] = result.get("manual_reason") or reason
            profile["performed_by"] = result.get("performed_by")
            monitor.show_card_match(profile)
            monitor.run_on_tk(lambda: done({"ok": True}))

        threading.Thread(target=work, daemon=True).start()

    def on_close():
        # The monitor owns the root window, so closing it ends the process -
        # stop the scan thread and release the camera on the way out.
        stop_event.set()
        camera.stop()
        stop_exit_camera()
        _write_last_session_summary(config, monitor)
        # Whoever was on duty here goes off duty with the gate monitor, so
        # the next entries aren't credited to someone who has left.
        shift_id = monitor.current_shift_id()
        if shift_id:
            try:
                api_client.gate_sign_out(config.gate_location, shift_id=shift_id, reason="gate_closed", timeout=3)
            except requests.RequestException:
                pass  # the backend ends it anyway when the gate monitor next opens, or at the time limit

    saved_layout, saved_dual = _load_layout()
    monitor = GateMonitorWindow(
        config.gate_location, config.direction,
        get_preview_frame=camera.get_preview_frame,
        on_tap=on_tap,
        officer_name=config.officer_name,
        camera_zoom=config.camera_zoom,
        get_camera_size=camera.capture_size,
        get_camera_fps=camera.measured_fps,
        version=config.app_version,
        on_close=on_close,
        camera_options=camera_options,
        on_camera_change=pick_camera,
        initial_camera_index=camera_index,
        video_fps=config.video_fps,
        student_display_fps=config.student_display_fps,
        on_sign_in=on_sign_in,
        on_sign_out=on_sign_out,
        on_exit_camera_change=pick_exit_camera,
        on_refresh_cameras=refresh_cameras,
        on_reader_setup=reader_setup,
        layout=saved_layout,
        dual_mode=saved_dual,
        on_layout_change=_save_layout,
        on_face_scan=on_face_scan,
        sound_volume=_load_sound_volume(),
        on_sound_change=_save_sound_volume,
        on_find_person=find_person,
        on_let_in=let_in,
    )
    monitor.set_reader_status(readers.status())
    # The exit camera chosen last time, if it's plugged in (and isn't the
    # entrance camera).
    # Two of the same webcam model have the same name: the remembered place
    # tells them apart, and the entrance camera is never taken for the exit's.
    exit_index = camera_select.find_by_name(cameras, camera_select.remembered_name(lanes.EXIT),
                                            place=camera_select.remembered_place(lanes.EXIT), avoid=camera_index)
    if exit_index is not None and exit_index != camera_index:
        start_exit_camera(exit_index)
    # Two cameras already plugged in but no exit camera yet: asked a few
    # seconds after opening; one plugged in later: within EXIT_SUGGEST_SECONDS.
    monitor.window.after(8000, suggest_exit_camera)
    monitor.window.after(EXIT_SUGGEST_SECONDS * 1000, watch_for_exit_camera)

    try:
        summary = api_client.gate_summary(config.gate_location)
        monitor.seed_stats(
            summary.get("entries_today", 0), summary.get("unknown_today", 0), summary.get("spoof_today", 0),
            summary.get("occlusion_today", 0), summary.get("exits_today", 0),
        )
    except requests.RequestException:
        pass  # stats just start at zero for this session if unreachable

    # A gate monitor that's (re)opening starts with nobody on duty: a shift
    # left open by a crash or a closed laptop lid ends here, so the next
    # guard signs in fresh. Also tells the status bar whether sign-in is on.
    try:
        monitor.set_gate_sign_in(api_client.gate_sign_out(config.gate_location, reason="gate_reopened"))
    except requests.RequestException:
        pass  # the scan loop picks the status up once the backend answers

    # The capstone speed/accuracy test (test_mode.py) - off unless asked for
    # (the launcher's "Open in TEST MODE" box, or --test-mode).
    test_run = None
    if "--test-mode" in sys.argv or os.environ.get("SECURETAP_TEST_MODE") == "1":
        import test_mode

        results_folder = Path(data_file("test-results"))
        test_run = test_mode.TestRun(
            results_folder, api_client, config.gate_location,
            lambda: test_mode.describe_setup(config, camera, live["camera_name"], camera_report(camera, live)),
        )
        monitor.set_test_mode()
        test_mode.TestWindow(monitor, test_run, api_client, results_folder)

    threading.Thread(
        target=scan_loop, args=(config, api_client, camera, monitor, stop_event, test_run, live), daemon=True
    ).start()
    # The dashboard's Cameras page: this gate's live picture, sent only while
    # an Admin is watching it (monitor_stream.py).
    threading.Thread(
        target=monitor_stream_loop, args=(config.gate_location, api_client, camera, stop_event), daemon=True
    ).start()

    status_busy = threading.Event()

    def status_worker():
        # Off the Tk thread: with the backend down, the health request takes
        # ~4s to fail on Windows ("localhost" tries IPv6, then IPv4), and
        # doing it on the Tk thread froze the whole gate monitor - video
        # included - for most of every 5s cycle. The monitor's set_* calls
        # are queue-based, so they're safe to make from here.
        try:
            exit_camera = exit_lane.get("camera")
            entrance_ok = camera.is_open()
            exit_ok = exit_camera is None or exit_camera.is_open()
            monitor.set_camera_ok(
                entrance_ok and exit_ok,
                "Cameras not connected" if not entrance_ok and not exit_ok
                else "Exit camera not connected" if not exit_ok
                else "Entrance camera not connected" if exit_camera is not None else "Camera not connected",
            )
            try:
                api_client.health()
                server_ok = True
            except requests.RequestException:
                server_ok = False
            if server_ok and any(_face_check_failing.values()):
                # Reachable, but its face check fails on every frame - the
                # cameras would otherwise look fine while nobody is recognized.
                monitor.set_backend_ok(False, "Face check error")
            else:
                monitor.set_backend_ok(server_ok)
            waiting = offline_queue.pending_count()
            monitor.set_queue_count(waiting)
            live["health"]["queue"] = waiting
        except Exception:
            traceback.print_exc()
        finally:
            status_busy.clear()

    def check_status():
        if monitor.is_closed():
            return
        # One check at a time - a slow one is skipped over, not piled up.
        if not status_busy.is_set():
            status_busy.set()
            threading.Thread(target=status_worker, daemon=True).start()
        # The NFC reader is a generic HID-keyboard-emulation device - Windows
        # sees it as just another keyboard, with no reliable, portable way to
        # query "is this specific reader plugged in" from Python, so it's shown
        # as ready rather than guessed at with an unreliable pseudo-check.
        monitor.window.after(STATUS_CHECK_INTERVAL_SECONDS * 1000, check_status)

    monitor.window.after(500, check_status)

    # Tells the system launcher (launcher.py at the repo root) it can stop its
    # loading animation. Everything slow is behind us at this point - the cv2
    # import, opening the webcam, building the window - so the monitor appears
    # within a frame of this line. The launcher matches on the marker rather
    # than timing a guess, so the spinner ends when the window is genuinely up.
    # Harmless noise when main.py is run directly. Keep in sync with
    # launcher.ENTRY_AGENT_READY_MARKER.
    print("SECURETAP_ENTRY_AGENT_READY - gate monitor open", flush=True)
    monitor.run()
    # The window closed normally (the guard closed it) - printed first thing,
    # before any clean-up that could still fail. Without this line, a gate
    # monitor that ends with an error code crashed, and the launcher reopens
    # it. Keep in sync with launcher.ENTRY_AGENT_CLOSED_MARKER.
    print("SECURETAP_GATE_MONITOR_CLOSED - window closed", flush=True)


if __name__ == "__main__":
    # A crash inside native code (OpenCV, the camera driver, Tk) ends the
    # process without Python's usual error report - this writes where it
    # happened to stderr, which the launcher keeps in its log. Its exit code
    # then tells the launcher it crashed rather than closed (launcher.py
    # GateStarter._watch_gate reopens it).
    if sys.stderr is not None:
        import faulthandler

        faulthandler.enable()
    main()
