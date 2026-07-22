import sys
import threading

import requests

from api_client import ApiClient
from camera import Camera
from config import load_config
from offline_queue import OfflineQueue
from ui import CardTapWindow, FeedbackWindow, MenuWindow

# The request itself now takes the bulk of the time (image capture is
# instant off the continuous stream, and detection is ~0.4-0.9s depending on
# frame size), so this is just a small breather between cycles rather than
# the main throttle - the old 2s value meant someone walking past at normal
# speed could cross the frame between samples entirely.
SCAN_INTERVAL_SECONDS = 0.2
STATUS_CHECK_INTERVAL_SECONDS = 5


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
    # A frame skipped for being too blurry/edge-cropped, or an unmatched
    # face that hasn't yet accumulated enough agreement to count as a real
    # "Unknown" - see IdentifyView._skip_reason/_confirm_or_vote_unmatched.
    # Neither is a decided outcome, so the UI shows "Checking..." instead of
    # flashing red on a single bad frame.
    retry = bool(result.get("retry"))
    photo_url = result.get("person_photo") if success else result.get("captured_photo")
    return {
        "box": box,
        "matched": success,
        "tiebreak": tiebreak,
        "retry": retry,
        "name": result.get("person_name") if success else "Unknown",
        "candidate_names": result.get("candidate_names") or [],
        "student_id": result.get("student_or_employee_id"),
        "department": result.get("department_or_course"),
        "confidence": confidence,
        "log_id": result.get("log_id"),
        "deduped": bool(result.get("deduped")),
        "direction": direction,
        "photo_bytes": _fetch_photo_cached(api_client, photo_cache, photo_url),
    }


def _build_profile(api_client, result, card_id):
    """Turns one /verify result into the profile dict CardTapWindow expects,
    fetching the actual photo bytes from the URL the backend returned."""
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
    }


def scan_loop(config, api_client, camera, ui, stop_event):
    """Runs continuously on its own thread: samples a frame and checks every
    face in it against every enrolled face (1:N search per face) - this is
    the primary gate check, no card tap required, and handles several
    people walking through together in the same frame. Kept off the Tk
    thread so a burst of faces during class change can't freeze the UI."""
    threshold_shown = False
    photo_cache = {}  # URL -> bytes, lives for this scan session
    while not stop_event.is_set():
        try:
            image_bytes, image_size = camera.capture_jpeg()
        except RuntimeError:
            stop_event.wait(SCAN_INTERVAL_SECONDS)
            continue

        try:
            response = api_client.identify(config.gate_location, config.direction, image_bytes)
            ui.show_offline(False)
            if not threshold_shown and response.get("threshold") is not None:
                ui.set_threshold(response["threshold"])
                threshold_shown = True
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

        stop_event.wait(SCAN_INTERVAL_SECONDS)


def handle_tap(config, api_client, offline_queue, tap_ui, nfc_id):
    """Runs off the Tk main thread. A card tap is a lookup that shows the
    guard the person's full result card for a manual cross-check - the
    actual entry decision comes from the continuous scan, not this."""
    tap_ui.show_status("Checking card...")
    try:
        result = api_client.verify(config.gate_location, config.direction, nfc_id=nfc_id)
    except requests.exceptions.HTTPError as exc:
        tap_ui.show_failure(_describe_http_error(exc.response))
        return
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        offline_queue.enqueue(nfc_id, config.gate_location, config.direction)
        tap_ui.show_failure("Offline - tap queued, will sync automatically.", "offline")
        return

    if not result.get("success"):
        tap_ui.show_failure(result.get("reason") or "Not enrolled.", result.get("reason_code"))
        return

    tap_ui.show_match(_build_profile(api_client, result, card_id=nfc_id))


def handle_manual(config, api_client, tap_ui, student_id):
    """The manual ID-entry fallback, for when the reader itself fails. Not
    offline-queued the way a tap is - this is an ad hoc guard action typed
    in the moment, not something worth replaying automatically later."""
    tap_ui.show_status("Checking ID...")
    try:
        result = api_client.verify(config.gate_location, config.direction, student_or_employee_id=student_id)
    except requests.exceptions.HTTPError as exc:
        tap_ui.show_failure(_describe_http_error(exc.response))
        return
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        tap_ui.show_failure("Offline - could not verify. Try again once connected.", "offline")
        return

    if not result.get("success"):
        tap_ui.show_failure(result.get("reason") or "Not enrolled.", result.get("reason_code"))
        return

    tap_ui.show_match(_build_profile(api_client, result, card_id="Manual entry"))


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
    config = load_config()
    if not config.service_token:
        print(
            "WARNING: SERVICE_TOKEN is not set in .env - the backend will reject requests.",
            file=sys.stderr,
        )

    api_client = ApiClient(config.api_base_url, config.service_token)
    camera = Camera(config.camera_index, exposure=config.camera_exposure)
    camera.start()  # opens the webcam once, independent of whether the
    # camera scanner window is currently open, so the feed is already warm
    # whenever it's launched from the menu.

    offline_queue = OfflineQueue(config.offline_db_path, api_client)
    offline_queue.start_background_sync()

    scan_state = {"ui": None, "stop_event": None}
    tap_state = {"ui": None}

    def open_camera_scanner():
        if scan_state["ui"] is not None:
            scan_state["ui"].window.deiconify()
            scan_state["ui"].window.lift()
            return
        stop_event = threading.Event()
        scan_ui = FeedbackWindow(
            menu.root, config.gate_location, config.direction,
            get_preview_frame=camera.get_preview_frame,
            on_close=close_camera_scanner,
        )
        scan_state["ui"] = scan_ui
        scan_state["stop_event"] = stop_event

        try:
            summary = api_client.gate_summary(config.gate_location)
            scan_ui.seed_stats(
                summary.get("entries_today", 0), summary.get("exits_today", 0), summary.get("unknown_today", 0)
            )
        except requests.RequestException:
            pass  # stats just start at zero for this session if unreachable

        threading.Thread(
            target=scan_loop, args=(config, api_client, camera, scan_ui, stop_event), daemon=True
        ).start()

    def close_camera_scanner():
        if scan_state["stop_event"] is not None:
            scan_state["stop_event"].set()
        scan_state["ui"] = None
        scan_state["stop_event"] = None

    def open_card_scanner():
        if tap_state["ui"] is not None:
            tap_state["ui"].window.deiconify()
            tap_state["ui"].window.lift()
            return
        tap_state["ui"] = CardTapWindow(
            menu.root, config.gate_location, config.direction,
            on_tap=on_tap, on_manual_submit=on_manual_submit, on_close=close_card_scanner,
        )

    def close_card_scanner():
        tap_state["ui"] = None

    def on_tap(nfc_id):
        threading.Thread(
            target=handle_tap, args=(config, api_client, offline_queue, tap_state["ui"], nfc_id), daemon=True
        ).start()

    def on_manual_submit(student_id):
        threading.Thread(
            target=handle_manual, args=(config, api_client, tap_state["ui"], student_id), daemon=True
        ).start()

    def on_exit():
        if scan_state["stop_event"] is not None:
            scan_state["stop_event"].set()
        camera.stop()
        menu.root.destroy()

    menu = MenuWindow(
        config.gate_location, config.officer_name, config.app_version,
        on_open_camera=open_camera_scanner, on_open_card=open_card_scanner, on_exit=on_exit,
    )

    def check_status():
        if not menu.root.winfo_exists():
            return
        menu.set_status("camera", camera.is_open())
        # The NFC reader is a generic HID-keyboard-emulation device - Windows
        # sees it as just another keyboard, with no reliable, portable way
        # to query "is this specific reader plugged in" from Python. Shown
        # as ready rather than building an unreliable pseudo-check that
        # would just be guessing.
        menu.set_status("reader", True)
        try:
            api_client.health()
            server_ok = True
        except requests.RequestException:
            server_ok = False
        menu.set_status("server", server_ok)
        if tap_state["ui"] is not None:
            tap_state["ui"].set_backend_ok(server_ok)
            tap_state["ui"].set_queue_count(offline_queue.pending_count())
        menu.root.after(STATUS_CHECK_INTERVAL_SECONDS * 1000, check_status)

    menu.root.after(500, check_status)
    menu.run_forever()


if __name__ == "__main__":
    main()
