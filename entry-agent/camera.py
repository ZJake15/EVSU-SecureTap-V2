import threading
import time

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
# full cost of uploading a full 1280x720 frame every scan.

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

    def __init__(self, index=0, exposure=None):
        self.index = index
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
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAPTURE_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAPTURE_HEIGHT)
        self._cap = cap
        if self.exposure is not None:
            self._apply_exposure(self.exposure)

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
        self._cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)
        self._cap.set(cv2.CAP_PROP_EXPOSURE, exposure)

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
            with self._lock:
                self._latest_frame = frame

    def get_preview_frame(self):
        """Latest raw frame, no overlay - the camera scanner window draws
        recognition boxes itself, from the backend's /api/identify results,
        not from anything computed here."""
        with self._lock:
            return None if self._latest_frame is None else self._latest_frame.copy()

    def capture_jpeg(self):
        """The latest raw frame, downscaled if needed and JPEG-encoded, for
        sending to the backend for identification. Returns (bytes, (width,
        height)) - callers need the encoded image's actual pixel dimensions
        to translate the backend's returned face locations back into screen
        coordinates, since this may be smaller than the raw captured frame."""
        frame = self.get_preview_frame()
        if frame is None:
            raise RuntimeError("No frame available yet - is the webcam initializing?")
        frame = _cap_dimension(frame, MAX_UPLOAD_DIMENSION)
        ok, buffer = cv2.imencode(".jpg", frame)
        if not ok:
            raise RuntimeError("Failed to encode the captured frame as JPEG.")
        height, width = frame.shape[:2]
        return buffer.tobytes(), (width, height)


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
