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

    def start(self):
        self._cap = cv2.VideoCapture(self.index, cv2.CAP_DSHOW)
        if not self._cap.isOpened():
            raise RuntimeError(f"Could not open webcam at index {self.index}.")
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAPTURE_WIDTH)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAPTURE_HEIGHT)
        if self.exposure is not None:
            self._apply_exposure(self.exposure)
        self._running = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

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

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        if self._cap:
            self._cap.release()

    def _read_loop(self):
        while self._running:
            success, frame = self._cap.read()
            if not success:
                time.sleep(0.1)
                continue
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


def _cap_dimension(frame, max_dimension):
    height, width = frame.shape[:2]
    largest = max(height, width)
    if largest <= max_dimension:
        return frame
    scale = max_dimension / largest
    return cv2.resize(frame, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_AREA)
