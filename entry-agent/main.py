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


def _build_profile(api_client, result):
    """Turns one /verify or /identify match into the profile dict
    FeedbackWindow expects, fetching the actual photo bytes from the URL the
    backend returned."""
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
    }


def _handle_scan_results(ui, results):
    """Only the count of matches (and failure reasons) reach the UI here -
    no names or photos. The continuous scan window is now just a glanceable
    gate indicator; who specifically was recognized lives on the dashboard's
    Live Monitoring page instead."""
    if not results:
        ui.show_status("Scanning...")
        return

    matches = [r for r in results if r.get("success")]
    if matches:
        ui.show_matches(len(matches))
        return

    # No one recognized. A lone "no face in frame at all" is just the normal
    # idle state, not worth flashing red about.
    if len(results) == 1 and results[0].get("reason") == "No face detected.":
        ui.show_status("Scanning...")
        return

    reason = (
        results[0].get("reason")
        if len(results) == 1
        else f"{len(results)} unrecognized faces detected."
    )
    ui.show_failure(reason or "Not enrolled.")


def scan_loop(config, api_client, camera, ui, stop_event):
    """Runs continuously on its own thread: samples a frame and checks every
    face in it against every enrolled face (1:N search per face) - this is
    the primary gate check now, no card tap required, and handles several
    people walking through together in the same frame."""
    while not stop_event.is_set():
        try:
            image_bytes = camera.capture_jpeg()
        except RuntimeError as exc:
            ui.show_failure(str(exc))
            stop_event.wait(SCAN_INTERVAL_SECONDS)
            continue

        try:
            response = api_client.identify(config.gate_location, config.direction, image_bytes)
            _handle_scan_results(ui, response.get("results", []))
        except requests.RequestException:
            # No offline queueing here - a stale queued frame from a live scan
            # has no value once connectivity returns; just keep sampling.
            ui.show_status("Offline - retrying scan...")

        stop_event.wait(SCAN_INTERVAL_SECONDS)


def handle_tap(config, api_client, offline_queue, tap_ui, nfc_id):
    """Runs off the Tk main thread. A card tap is now a lookup that shows the
    guard the person's full profile for a manual cross-check - the actual
    entry decision comes from the continuous scan, not this. Shown in its
    own CardTapWindow, separate from the main scan display."""
    tap_ui.show_status("Checking card...")
    try:
        result = api_client.verify(nfc_id, config.gate_location, config.direction)
    except requests.exceptions.HTTPError as exc:
        tap_ui.show_failure(_describe_http_error(exc.response))
        return
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        offline_queue.enqueue(nfc_id, config.gate_location, config.direction)
        tap_ui.show_failure("Offline - tap queued, will sync automatically.")
        return

    if not result.get("success"):
        tap_ui.show_failure(result.get("reason") or "Not enrolled.")
        return

    tap_ui.show_match(_build_profile(api_client, result))


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
    camera = Camera(config.camera_index)
    camera.start()  # opens the webcam once, independent of whether the
    # camera scanner window is currently open, so the feed is already warm
    # (no "starting camera..." delay) whenever it's launched from the menu.

    offline_queue = OfflineQueue(config.offline_db_path, api_client)
    offline_queue.start_background_sync()

    # Mutable holders (not plain locals) so the nested open/close/on_tap
    # functions below always see the current window/thread, even after a
    # scanner is closed and reopened as a fresh instance.
    scan_state = {"ui": None, "stop_event": None}
    tap_state = {"ui": None}

    def open_camera_scanner():
        if scan_state["ui"] is not None:
            scan_state["ui"].root.deiconify()
            scan_state["ui"].root.lift()
            return
        stop_event = threading.Event()
        scan_ui = FeedbackWindow(
            menu.root, config.gate_location,
            get_preview_frame=camera.get_preview_frame,
            on_close=close_camera_scanner,
        )
        scan_state["ui"] = scan_ui
        scan_state["stop_event"] = stop_event
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
            menu.root, config.gate_location, on_tap=on_tap, on_close=close_card_scanner
        )

    def close_card_scanner():
        tap_state["ui"] = None

    def on_tap(nfc_id):
        tap_ui = tap_state["ui"]
        if tap_ui is None:
            return
        threading.Thread(
            target=handle_tap,
            args=(config, api_client, offline_queue, tap_ui, nfc_id),
            daemon=True,
        ).start()

    def on_exit():
        if scan_state["stop_event"] is not None:
            scan_state["stop_event"].set()
        camera.stop()
        menu.root.destroy()

    menu = MenuWindow(
        config.gate_location,
        on_open_camera=open_camera_scanner,
        on_open_card=open_card_scanner,
        on_exit=on_exit,
    )
    menu.run_forever()


if __name__ == "__main__":
    main()
