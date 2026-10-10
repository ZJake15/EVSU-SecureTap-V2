import json
import os
import sys
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path

import requests

import camera_select
from api_client import ApiClient
from camera import Camera, list_available_cameras
from config import data_file, load_config
from offline_queue import OfflineQueue
from ui import GateMonitorWindow, set_app_user_model_id

# The pause between face checks is config.scan_pause_seconds (0.2 s unless
# the launcher's speed mode says otherwise). The request itself takes the
# bulk of each cycle (image capture is instant off the continuous stream), so
# the pause is a small breather rather than the main throttle - the old 2s
# value meant someone walking past at normal speed could cross the frame
# between samples entirely.
STATUS_CHECK_INTERVAL_SECONDS = 5

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


def _extract_image_size(response, fallback):
    """The backend returns image_size as {"width": W, "height": H} (JSON has
    no tuple type), but the UI wants a plain (width, height) pair - falls
    back to the size camera.capture_jpeg() reported if the key's missing."""
    size = response.get("image_size")
    if isinstance(size, dict) and "width" in size and "height" in size:
        return size["width"], size["height"]
    return fallback


def _fetch_photo_cached(api_client, photo_cache, url):
    """Fetches a reference/captured photo's bytes, cached by URL so a
    person (or lingering unrecognized face, which server-side dedup already
    keys to the same captured_photo URL across frames) is only fetched once
    per camera-scanner session, not once per ~0.2s poll cycle."""
    if not url:
        return None
    if url in photo_cache:
        return photo_cache[url]
    try:
        photo_bytes = api_client.fetch_photo(url)
    except requests.RequestException:
        photo_bytes = None
    photo_cache[url] = photo_bytes
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
    photo_url = result.get("person_photo") if success else result.get("captured_photo")
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
        "name": (
            result.get("person_name")
            if success
            else "Please uncover your face" if occlusion_suspected
            else "Possible spoof" if spoof_suspected
            else "Unknown"
        ),
        "candidate_names": result.get("candidate_names") or [],
        "student_id": result.get("student_or_employee_id"),
        "department": result.get("department_or_course"),
        "distinguishing_note": result.get("distinguishing_note"),
        "confidence": confidence,
        "log_id": result.get("log_id"),
        "deduped": bool(result.get("deduped")),
        # Settings page: "Alert on repeated unknown faces" - the backend sets
        # this when the same unrecognized face keeps coming back.
        "repeated_unknown": bool(result.get("repeated_unknown")),
        "repeated_unknown_count": result.get("repeated_unknown_count"),
        "direction": direction,
        "photo_bytes": _fetch_photo_cached(api_client, photo_cache, photo_url),
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
    }


def scan_loop(config, api_client, camera, ui, stop_event):
    """Runs continuously on its own thread: samples a frame and checks every
    face in it against every enrolled face (1:N search per face) - this is
    the primary gate check, no card tap required, and handles several
    people walking through together in the same frame. Kept off the Tk
    thread so a burst of faces during class change can't freeze the UI."""
    # The backend sends its current Settings-page values with every answer,
    # so a change made on the dashboard reaches this gate monitor within a
    # frame or two - no restart. Only passed on to the UI when they change.
    last_threshold = None
    last_alerts = None
    last_sign_in = None
    photo_cache = {}  # URL -> bytes, lives for this scan session
    while not stop_event.is_set():
        try:
            image_bytes, image_size = camera.capture_jpeg()
        except RuntimeError:
            stop_event.wait(config.scan_pause_seconds)
            continue

        try:
            response = api_client.identify(config.gate_location, config.direction, image_bytes)
            ui.show_offline(False)
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
            recognitions = [
                r
                for r in (
                    _build_recognition(config.direction, item, api_client, photo_cache)
                    for item in response.get("results", [])
                )
                if r is not None
            ]
            ui.show_recognitions(recognitions, _extract_image_size(response, image_size))
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

        stop_event.wait(config.scan_pause_seconds)


def handle_tap(config, api_client, offline_queue, ui, nfc_id):
    """Runs off the Tk main thread. A card tap is a lookup that shows the
    guard the person's details for a cross-check - the actual entry decision
    comes from the continuous scan, not this. Either way the outcome lands
    in the monitor's live log, so a tap is as visible as a face event."""
    ui.show_card_status("Checking card...")
    try:
        result = api_client.verify(config.gate_location, config.direction, nfc_id=nfc_id)
    except requests.exceptions.HTTPError as exc:
        ui.show_card_failure(_describe_http_error(exc.response), "server_error")
        return
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        offline_queue.enqueue(nfc_id, config.gate_location, config.direction)
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
            else:
                ui.show_card_failure(result.get("reason") or "Staff ID card.", result.get("reason_code"))
            return
        if not result.get("success"):
            ui.show_card_failure(result.get("reason") or "Not enrolled.", result.get("reason_code"))
            return
        ui.show_card_match(
            _build_profile(api_client, result, card_id=nfc_id, direction=config.direction)
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


def handle_sign_out(config, api_client, ui, shift_id):
    try:
        ui.set_gate_sign_in(api_client.gate_sign_out(config.gate_location, shift_id=shift_id))
    except requests.RequestException:
        traceback.print_exc()


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
    camera_index = camera_select.choose(cameras, camera_select.remembered_name(), fallback_index=config.camera_index)
    camera_names = {index: name for index, name, _kind in cameras}
    camera_options = [(index, f"{name} ({camera_select.LABELS[kind]})") for index, name, kind in cameras]
    print(f"camera: using {camera_names.get(camera_index, f'#{camera_index}')}", file=sys.stderr)

    camera = Camera(
        camera_index, exposure=config.camera_exposure, max_upload_dimension=config.upload_max_dimension
    )

    def pick_camera(index):
        """The guard chose a camera in the gate monitor - use it now and
        prefer it next time."""
        camera.set_index(index)
        if index in camera_names:
            camera_select.remember(camera_names[index])
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

    def on_tap(nfc_id):
        threading.Thread(
            target=handle_tap, args=(config, api_client, offline_queue, monitor, nfc_id), daemon=True
        ).start()

    def on_sign_in(username, password):
        threading.Thread(
            target=handle_sign_in, args=(config, api_client, monitor, username, password), daemon=True
        ).start()

    def on_sign_out(shift_id):
        threading.Thread(target=handle_sign_out, args=(config, api_client, monitor, shift_id), daemon=True).start()

    def on_close():
        # The monitor owns the root window, so closing it ends the process -
        # stop the scan thread and release the camera on the way out.
        stop_event.set()
        camera.stop()
        _write_last_session_summary(config, monitor)
        # Whoever was on duty here goes off duty with the gate monitor, so
        # the next entries aren't credited to someone who has left.
        shift_id = monitor.current_shift_id()
        if shift_id:
            try:
                api_client.gate_sign_out(config.gate_location, shift_id=shift_id, reason="gate_closed", timeout=3)
            except requests.RequestException:
                pass  # the backend ends it anyway when the gate monitor next opens, or at the time limit

    monitor = GateMonitorWindow(
        config.gate_location, config.direction,
        get_preview_frame=camera.get_preview_frame,
        on_tap=on_tap,
        officer_name=config.officer_name,
        version=config.app_version,
        on_close=on_close,
        camera_options=camera_options,
        on_camera_change=pick_camera,
        initial_camera_index=camera_index,
        video_fps=config.video_fps,
        student_display_fps=config.student_display_fps,
        on_sign_in=on_sign_in,
        on_sign_out=on_sign_out,
    )

    try:
        summary = api_client.gate_summary(config.gate_location)
        monitor.seed_stats(
            summary.get("entries_today", 0), summary.get("unknown_today", 0), summary.get("spoof_today", 0),
            summary.get("occlusion_today", 0),
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

    threading.Thread(
        target=scan_loop, args=(config, api_client, camera, monitor, stop_event), daemon=True
    ).start()

    status_busy = threading.Event()

    def status_worker():
        # Off the Tk thread: with the backend down, the health request takes
        # ~4s to fail on Windows ("localhost" tries IPv6, then IPv4), and
        # doing it on the Tk thread froze the whole gate monitor - video
        # included - for most of every 5s cycle. The monitor's set_* calls
        # are queue-based, so they're safe to make from here.
        try:
            monitor.set_camera_ok(camera.is_open())
            try:
                api_client.health()
                server_ok = True
            except requests.RequestException:
                server_ok = False
            monitor.set_backend_ok(server_ok)
            monitor.set_queue_count(offline_queue.pending_count())
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


if __name__ == "__main__":
    main()
