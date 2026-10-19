import threading
import time
from collections import deque

import cv2

CAPTURE_WIDTH = 1280  # requested from the webcam driver - many DSHOW webcams
CAPTURE_HEIGHT = 720  # otherwise default to 640x480, which leaves a face 10ft
# from the camera as a tiny cluster of pixels before any recognition even
# starts. Falls back to the camera's nearest supported resolution if 720p
# isn't available.
MAX_UPLOAD_DIMENSION = 960  # face_recognition's HOG detector roughly doubles in
# speed going from an ~1300px phone-photo-sized frame down to 640px, but 640
# was cutting too much detail out of faces several feet away - 960 is a
# middle ground that keeps distance recognition working without paying the
# full cost of uploading a full 1280x720 frame every scan. Overridable per PC
# with UPLOAD_MAX_DIMENSION in entry-agent/.env - 640 suits a low-power
# laptop where people stand close to the camera (see documentation.md).
# JPEG quality of each frame sent to the backend - 85 is visually the same
# to the face AI as OpenCV's default 95, at a noticeably smaller file.
UPLOAD_JPEG_QUALITY = 85
# Camera zoom (the launcher's Entry Agent settings, per gate): the middle
# 1/zoom of each frame, cropped before anything is shrunk - so a face farther
# away arrives at the face check bigger, in real pixels. 2x is the most: a
# 1280x720 frame's middle half is 640x360, still at least the 640 px sent in
# Light mode; past that the picture would only be enlarged, which adds blur.
MIN_ZOOM, MAX_ZOOM = 1.0, 2.0

# The Settings page's Camera values that change how the camera itself is
# opened (configure() reconnects it when one changes). "auto" = as before
# this group existed: 1280x720, the driver's own frame rate, exposure,
# backlight and focus.
CAPTURE_KEYS = ("camera_resolution", "camera_fps", "camera_exposure", "backlight_compensation", "camera_focus")
# DirectShow's exposure mode values for CAP_PROP_AUTO_EXPOSURE. Many cameras
# read it back as -1 whatever it is (the EMEET does), so its "own" mode can't
# be remembered and put back - Auto switches automatic exposure on instead.
AUTO_EXPOSURE_ON, AUTO_EXPOSURE_OFF = 0.75, 0.25

# How often to retry opening the camera while it isn't connected - covers
# both "never was" (nothing plugged in at startup) and "used to be, isn't
# right now" (unplugged mid-session) with the same retry loop, since from
# here they look identical: cv2.VideoCapture just won't open.
RECONNECT_INTERVAL_SECONDS = 3.0
# Consecutive failed reads (at ~0.1s apart - see _run_loop) before treating
# the device as genuinely gone rather than one dropped frame. ~2s of nothing
# but failures is well past what a real, still-connected webcam ever does.
MAX_CONSECUTIVE_READ_FAILURES = 20


class Camera:
    """Keeps the webcam open continuously (instead of opening/closing per
    shot) so the entry-agent can show a live preview while also periodically
    grabbing a frame for the backend to identify.

    Face detection and recognition both happen entirely server-side now
    (dlib, via /api/identify) - there's no local Haar-cascade detector here
    anymore. The camera scanner window draws its bounding boxes directly
    from the backend's response instead of running a second, weaker
    detector locally just for the preview; that used to cause the preview
    box to disappear, jitter, or flag non-face objects independently of
    what was actually being recognized. One detector, one source of truth.
    """

    def __init__(self, index=0, exposure=None, max_upload_dimension=MAX_UPLOAD_DIMENSION, zoom=1.0):
        self.index = index
        self.max_upload_dimension = max_upload_dimension
        self.zoom = min(MAX_ZOOM, max(MIN_ZOOM, float(zoom or 1.0)))
        # Optional, off by default - see _apply_exposure below for why this
        # can't be a value picked in advance for every webcam.
        self.exposure = exposure
        self._cap = None
        self._lock = threading.Lock()
        self._latest_frame = None
        self._running = False
        self._thread = None
        # An instance attribute (not a local in _run_loop) specifically so
        # set_index() can reset it from the Tk thread and make the very next
        # loop tick retry immediately, instead of waiting out whatever's left
        # of the current RECONNECT_INTERVAL_SECONDS window.
        self._next_open_attempt = 0.0
        # Settings > Camera (configure()); _reopen asks _run_loop to reconnect
        # with them, on its own thread, between two reads.
        self._capture = {key: "auto" for key in CAPTURE_KEYS}
        self._reopen = False
        self._applied = {}  # exposure / backlight / focus -> "applied" | "ignored" | "camera"
        self._originals = {}  # the camera's own values, put back when a setting returns to Auto
        self._frame_times = deque(maxlen=90)  # for the measured frame rate

    def start(self):
        """No camera plugged in (or the wrong index configured) is a real,
        expected deployment state - a gate can still run on NFC taps and
        manual overrides alone - so this never raises, and never blocks on
        the actual webcam open either (that now happens inside _run_loop,
        off this calling thread). is_open() reports False until it actually
        connects (self._cap stays None until then), and get_preview_frame()
        correctly returns None the whole time - which is what
        GateMonitorWindow._update_video keys off to show its "No camera
        connected" placeholder instead of a frozen/blank feed. The same
        background loop keeps retrying for as long as the app runs, so a
        camera plugged in after startup - or unplugged and back again mid-
        session - is picked up without a restart."""
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def _try_open(self):
        """One attempt to (re)connect. Cheap enough to call on a timer
        indefinitely - a failed cv2.VideoCapture().isOpened() check returns
        near-instantly, it doesn't hang waiting for a device that isn't
        there."""
        cap = cv2.VideoCapture(self.index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap.release()
            return
        wanted = dict(self._capture)
        width, height = _resolution(wanted["camera_resolution"])
        if wanted["camera_resolution"] != "auto" or wanted["camera_fps"] != "auto":
            # Most webcams only give big pictures at a full frame rate in their
            # compressed (MJPG) mode - asked for first, as DirectShow needs.
            # One that can't just stays in its own mode.
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        if wanted["camera_fps"] != "auto":
            cap.set(cv2.CAP_PROP_FPS, float(wanted["camera_fps"]))
        self._cap = cap
        self._frame_times.clear()
        self._applied = self._apply_picture_settings(wanted)

    def _apply_picture_settings(self, wanted):
        """Exposure, backlight and focus - each driver decides whether it
        takes them, so each is read back and reported ("applied" or
        "ignored"). Auto puts back what the camera had before this program
        changed it (drivers often keep a value even after the camera's
        closed), or leaves it alone if it never was."""
        applied = {}
        exposure = wanted["camera_exposure"]
        if exposure != "auto":
            applied["exposure"] = self._apply_exposure(float(exposure))
        elif self.exposure is not None:  # CAMERA_EXPOSURE in this computer's gate settings
            self._apply_exposure(self.exposure)
            applied["exposure"] = "camera"
        else:
            # The driver keeps a manual exposure even after the camera is
            # closed, so Auto always switches automatic exposure back on.
            self._cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, AUTO_EXPOSURE_ON)
            applied["exposure"] = "camera"
        backlight = wanted["backlight_compensation"]
        if backlight != "auto":
            applied["backlight"] = self._set_checked(cv2.CAP_PROP_BACKLIGHT, 1 if backlight == "on" else 0)
        else:
            self._restore(cv2.CAP_PROP_BACKLIGHT)
            applied["backlight"] = "camera"
        if wanted["camera_focus"] == "locked":
            applied["focus"] = self._set_checked(cv2.CAP_PROP_AUTOFOCUS, 0)
        else:
            self._restore(cv2.CAP_PROP_AUTOFOCUS)
            applied["focus"] = "camera"
        return applied

    def _set_checked(self, prop, value):
        """Sets a camera property and reads it back: "applied" if the camera
        kept it, "ignored" if not. Remembers the camera's own value first."""
        cap = self._cap
        if prop not in self._originals:
            self._originals[prop] = cap.get(prop)
        accepted = cap.set(prop, value)
        return "applied" if accepted and abs(cap.get(prop) - value) < 0.5 else "ignored"

    def _restore(self, prop):
        if prop in self._originals:
            self._cap.set(prop, self._originals[prop])

    def configure(self, settings):
        """Settings > Camera, as sent with every scan answer. Reconnects the
        camera - the picture blinks for about a second - only when one of
        the CAPTURE_KEYS changed. Returns whether it will."""
        wanted = {key: str(settings.get(key) or "auto") for key in CAPTURE_KEYS}
        if wanted == self._capture:
            return False
        self._capture = wanted
        self._reopen = True
        self._next_open_attempt = 0.0
        return True

    def measured_fps(self):
        """Frames a second the camera really delivers (over its last ~90
        frames), or None until there are enough to tell."""
        times = list(self._frame_times)
        if len(times) < 10 or times[-1] <= times[0]:
            return None
        return (len(times) - 1) / (times[-1] - times[0])

    def report(self):
        """What this camera actually runs at - for Settings > Camera."""
        size = self.capture_size()
        fps = self.measured_fps()
        return {
            "resolution": f"{size[0]}x{size[1]}" if size else None,
            "resolution_asked": self._capture["camera_resolution"],
            "fps": round(fps, 1) if fps else None,
            "fps_asked": self._capture["camera_fps"],
            "zoom": self.zoom,
            **self._applied,
        }

    def _apply_exposure(self, exposure):
        """Best-effort, experimental: a shorter exposure time (faster
        shutter) reduces motion blur on someone walking through the gate,
        at the cost of a darker image unless the room is well-lit. This is
        NOT set by default because DirectShow's manual-exposure support is
        highly webcam/driver-dependent - some accept it fine, some clamp to
        a small set of driver-specific values, some silently ignore it and
        stay on auto. 0.25 is DirectShow's conventional "manual mode" sentinel
        for CAP_PROP_AUTO_EXPOSURE (not a real exposure value itself).
        CAP_PROP_EXPOSURE's own units/range are also driver-specific (often
        a power-of-two exponent, e.g. -4 to -7) - check your webcam's
        control panel/driver docs, then confirm the change actually took by
        watching the preview darken or reading back
        cap.get(cv2.CAP_PROP_EXPOSURE) after this call."""
        self._cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, AUTO_EXPOSURE_OFF)
        accepted = self._cap.set(cv2.CAP_PROP_EXPOSURE, exposure)
        return "applied" if accepted and abs(self._cap.get(cv2.CAP_PROP_EXPOSURE) - exposure) < 0.5 else "ignored"

    def is_open(self):
        return bool(self._cap and self._cap.isOpened())

    def set_index(self, new_index):
        """Switches to a different camera device - e.g. the user picked a
        different one from the entry-agent's camera dropdown. Reuses the
        exact same close-then-reconnect path a genuine disconnect already
        goes through (_close_after_failure): from _run_loop's perspective
        this looks identical to "the camera changed", because that's
        exactly what happened. Takes effect within the next loop tick
        (~100ms) rather than waiting out the normal reconnect interval."""
        if new_index == self.index:
            return
        self.index = new_index
        self._close_after_failure()
        self._next_open_attempt = 0.0

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        if self._cap:
            self._cap.release()
            self._cap = None

    def _close_after_failure(self):
        """Two callers, same cleanup either way: the device stopped
        responding entirely (unplugged mid-session, a driver crash - as
        opposed to one bad frame, which the read loop below already absorbs
        on its own), or set_index() was called to deliberately switch to a
        different device. Releases the stale handle and clears the latest
        frame so get_preview_frame() genuinely goes back to returning None
        (a frozen last frame would otherwise sit on screen forever, looking
        like a live feed that simply stopped updating). _run_loop's next
        iteration then falls into the same reconnect attempt as if this had
        never been connected at all - at whatever self.index is by then."""
        if self._cap:
            self._cap.release()
        self._cap = None
        with self._lock:
            self._latest_frame = None

    def _run_loop(self):
        """One persistent loop for the whole time the app runs, covering
        both states a plain read-loop used to assume never changes: reading
        frames while connected, and periodically retrying the connection
        while not. Runs on its own thread so a slow/hanging open attempt
        can never freeze the Tk UI or the scan loop."""
        consecutive_failures = 0
        while self._running:
            if self._reopen:  # Settings > Camera changed (configure)
                self._reopen = False
                self._close_after_failure()
                self._next_open_attempt = 0.0
            if self._cap is None:
                now = time.monotonic()
                if now >= self._next_open_attempt:
                    self._try_open()
                    self._next_open_attempt = now + RECONNECT_INTERVAL_SECONDS
                else:
                    time.sleep(0.1)
                continue

            success, frame = self._cap.read()
            if not success:
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_READ_FAILURES:
                    self._close_after_failure()
                    consecutive_failures = 0
                else:
                    time.sleep(0.1)
                continue

            consecutive_failures = 0
            self._frame_times.append(time.monotonic())
            with self._lock:
                self._latest_frame = frame

    def get_preview_frame(self):
        """Latest frame (zoomed - see _zoomed), no overlay - the camera
        scanner window draws recognition boxes itself, from the backend's
        /api/identify results, not from anything computed here. The guard's
        view, the student display and capture_jpeg() all come through here,
        so what's shown is exactly what's checked and the boxes line up."""
        with self._lock:
            frame = self._latest_frame
        return None if frame is None else self._zoomed(frame).copy()

    def capture_size(self):
        """(width, height) of the camera's raw frames - what it actually
        delivers, which can differ from the 1280x720 asked for - or None
        before the first frame. For the test mode's records."""
        with self._lock:
            frame = self._latest_frame
        return None if frame is None else (frame.shape[1], frame.shape[0])

    def _zoomed(self, frame):
        """The middle 1/zoom of the frame - the whole frame at 1x."""
        if self.zoom <= MIN_ZOOM:
            return frame
        height, width = frame.shape[:2]
        crop_w, crop_h = round(width / self.zoom), round(height / self.zoom)
        left, top = (width - crop_w) // 2, (height - crop_h) // 2
        return frame[top:top + crop_h, left:left + crop_w]

    def capture_jpeg(self):
        """The latest raw frame, downscaled if needed and JPEG-encoded, for
        sending to the backend for identification. Returns (bytes, (width,
        height)) - callers need the encoded image's actual pixel dimensions
        to translate the backend's returned face locations back into screen
        coordinates, since this may be smaller than the raw captured frame."""
        frame = self.get_preview_frame()
        if frame is None:
            raise RuntimeError("No frame available yet - is the webcam initializing?")
        frame = _cap_dimension(frame, self.max_upload_dimension)
        ok, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, UPLOAD_JPEG_QUALITY])
        if not ok:
            raise RuntimeError("Failed to encode the captured frame as JPEG.")
        height, width = frame.shape[:2]
        return buffer.tobytes(), (width, height)


def _resolution(value):
    """Settings > Camera's "1920x1080" -> (1920, 1080); "auto" -> 720p."""
    try:
        width, height = (int(part) for part in str(value).lower().split("x"))
        return width, height
    except ValueError:
        return CAPTURE_WIDTH, CAPTURE_HEIGHT


def list_available_cameras(max_probe=10):
    """Every camera device Windows can currently see, as (index, label)
    pairs - index is what Camera(index=...) / Camera.set_index(...) expect,
    label is what a human should see in the entry-agent's camera dropdown.

    Prefers real device names via pygrabber's DirectShow enumeration (e.g.
    "Logitech BRIO", "Integrated Webcam") - a guard choosing between two
    unlabeled "Camera 0"/"Camera 1" entries has no way to know which is
    which, which defeats the point of a picker on a laptop with more than
    one camera. Falls back to generic index-based labels if pygrabber isn't
    installed or can't enumerate anything for any reason - this is a
    best-effort nicety, never something the picker should crash over."""
    try:
        from pygrabber.dshow_graph import FilterGraph
        names = FilterGraph().get_input_devices()
        if names:
            return list(enumerate(names))
    except Exception:
        pass

    # Fallback: probe indices directly - no real names, and each failed
    # probe still costs a DirectShow open/close, but works with zero extra
    # dependencies if pygrabber isn't installed or fails to enumerate.
    available = []
    for index in range(max_probe):
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        if cap.isOpened():
            available.append((index, f"Camera {index}"))
        cap.release()
    return available


def _cap_dimension(frame, max_dimension):
    height, width = frame.shape[:2]
    largest = max(height, width)
    if largest <= max_dimension:
        return frame
    scale = max_dimension / largest
    return cv2.resize(frame, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_AREA)
