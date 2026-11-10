"""Sends this gate's live camera picture to the dashboard's Cameras page -
only while an Admin has it open (backend logs/camera_monitor.py).

While nobody is watching, it only asks once a second (an empty request), so
an unwatched gate does no extra work. While someone is, it sends the newest
camera picture - the same view the face checks use, zoom included, so the
dashboard's face boxes line up - as a JPEG, as many times a second and as
wide as Settings > Camera says (Cameras page smoothness / picture width).

Runs on its own thread with its own connection, so it never waits behind a
face check (and a face check never waits behind it).
"""

import time

import cv2
import requests

from camera import _cap_dimension

ASK_EVERY_SECONDS = 1.0     # while nobody is watching
RETRY_AFTER_SECONDS = 3.0   # backend unreachable (or older, without the Cameras page)
JPEG_QUALITY = 70
DEFAULT_FPS, DEFAULT_WIDTH = 15, 960


def _clamped(value, low, high, fallback):
    try:
        return min(high, max(low, int(value)))
    except (TypeError, ValueError):
        return fallback


def monitor_stream_loop(gate_location, api_client, camera, stop_event):
    session = requests.Session()
    url = f"{api_client.base_url}/gate/monitor-frame"
    headers = {"X-Service-Token": api_client.service_token, "Content-Type": "image/jpeg"}
    params = {"gate_location": gate_location}
    watched, fps, width = False, DEFAULT_FPS, DEFAULT_WIDTH
    last_frame_number = None
    next_at = time.monotonic()
    while not stop_event.is_set():
        body = b""
        if watched:
            # The camera's own frame counter: a picture already sent isn't
            # sent again when the camera delivers fewer a second than asked.
            frame_number = getattr(camera, "_frames_since_open", None)
            frame = camera.get_preview_frame() if frame_number != last_frame_number or frame_number is None else None
            if frame is not None:
                ok, buffer = cv2.imencode(".jpg", _cap_dimension(frame, width), [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
                if ok:
                    body = buffer.tobytes()
                    last_frame_number = frame_number
        try:
            response = session.post(url, params=params, headers=headers, data=body, timeout=5)
            response.raise_for_status()
            answer = response.json()
        except (requests.RequestException, ValueError):
            watched = False
            stop_event.wait(RETRY_AFTER_SECONDS)
            next_at = time.monotonic()
            continue
        watched = bool(answer.get("watched"))
        fps = _clamped(answer.get("fps"), 1, 30, DEFAULT_FPS)
        width = _clamped(answer.get("width"), 320, 1920, DEFAULT_WIDTH)
        gap = 1.0 / fps if watched else ASK_EVERY_SECONDS
        # On a fixed timetable, not "wait a gap after each one": Windows
        # rounds short waits up (~15 ms), which would otherwise add up -
        # 15 asked came out as about 13 a second.
        now = time.monotonic()
        next_at += gap
        if next_at < now - gap:
            next_at = now  # far behind (a slow answer) - carry on, without a burst to catch up
        stop_event.wait(max(0.0, next_at - now))
