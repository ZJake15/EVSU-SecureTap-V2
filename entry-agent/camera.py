import threading
import time

import cv2

FACE_BOX_COLOR = (34, 197, 94)  # green, BGR order (cv2 draws in BGR)
SMOOTHING_ALPHA = 0.35  # higher = snappier, lower = smoother but laggier
BOX_HOLD_FRAMES = 10  # keep showing a track through this many consecutive misses -
# a forgiving hold is the right way to smooth over occasional misses, rather
# than making the detector itself stricter (that just suppresses real faces)
MAX_MATCH_DISTANCE = 120  # px - how far a face can move between frames and still
# count as "the same face" rather than a new one
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


class _Track:
    __slots__ = ("box", "misses")

    def __init__(self, box):
        self.box = box
        self.misses = 0


class Camera:
    """Keeps the webcam open continuously (instead of opening/closing per
    shot) so the entry-agent can show a live preview while also periodically
    grabbing a frame for the backend to identify.

    Face *detection* (Haar cascade, via OpenCV - no dlib needed) runs on the
    same background thread that reads frames, not the Tk UI thread, so it
    can't lag the window. Detections are matched frame-to-frame by proximity
    (nearest centroid) against the faces already being tracked, and each
    track keeps its own smoothed box (an exponential moving average, held
    briefly through short misses). This is what keeps boxes stable: picking
    "whichever detection is largest" fresh every frame - the previous
    approach - meant the box could flip between different faces, or between
    a face's own slightly-different-sized detections frame to frame, since
    Haar cascades are inherently noisy about exact box size/position even for
    a stationary face. Actual face *recognition* still happens server-side
    against the enrolled encodings, for every face in the frame - this is
    only the live preview overlay.
    """

    def __init__(self, index=0):
        self.index = index
        self._cap = None
        self._lock = threading.Lock()
        self._latest_frame = None
        self._latest_boxes = []
        self._running = False
        self._thread = None
        self._face_cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )
        # Used only to verify a face-shaped region actually has an eye-like
        # feature in it - see _looks_like_a_face().
        self._eye_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_eye.xml")
        # Only ever touched from the background read thread - no lock needed.
        self._tracks = []

    def start(self):
        self._cap = cv2.VideoCapture(self.index, cv2.CAP_DSHOW)
        if not self._cap.isOpened():
            raise RuntimeError(f"Could not open webcam at index {self.index}.")
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAPTURE_WIDTH)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAPTURE_HEIGHT)
        self._running = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

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
            boxes = self._detect_faces(frame)
            with self._lock:
                self._latest_frame = frame
                self._latest_boxes = boxes

    def _detect_faces(self, frame):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)  # steadier detection across lighting conditions
        # minSize is deliberately small: a face 10ft from the camera is a
        # small cluster of pixels, and a large minSize was filtering those
        # out along with the non-face clutter it was meant to suppress. Raw
        # Haar face detections alone are too prone to flagging textured
        # objects/backgrounds though, so every candidate is additionally
        # checked by _looks_like_a_face() before being accepted - that's what
        # actually distinguishes faces from objects, not the size threshold.
        candidates = self._face_cascade.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=5, minSize=(50, 50)
        )
        detections = [tuple(d) for d in candidates if self._looks_like_a_face(gray, d)]
        self._update_tracks(detections)
        return [track.box for track in self._tracks]

    def _looks_like_a_face(self, gray, box):
        """A face-shaped region isn't necessarily a face - Haar cascades are
        notorious for flagging textured objects/backgrounds. Requiring at
        least one eye-like feature inside the candidate region is a cheap,
        standard way to reject those false positives without raising
        minSize/minNeighbors, which would also reject real faces that are
        just small because someone's further from the camera."""
        x, y, w, h = box
        # Eyes sit in roughly the upper 65% of a face - restricting the
        # search region also cuts down on false eye-like matches elsewhere
        # (nostrils, mouth, collar) that could otherwise confirm a
        # non-face shape as a "face".
        upper = gray[y : y + int(h * 0.65), x : x + w]
        if upper.size == 0:
            return False
        eyes = self._eye_cascade.detectMultiScale(
            upper, scaleFactor=1.1, minNeighbors=4, minSize=(max(8, w // 10), max(8, h // 10))
        )
        return len(eyes) > 0

    def _update_tracks(self, detections):
        unmatched_detections = list(range(len(detections)))
        matched_tracks = set()

        for track in self._tracks:
            match_index = self._closest_detection(track.box, detections, unmatched_detections)
            if match_index is None:
                continue
            unmatched_detections.remove(match_index)
            matched_tracks.add(id(track))
            track.box = self._smooth(track.box, detections[match_index])
            track.misses = 0

        for track in self._tracks:
            if id(track) not in matched_tracks:
                track.misses += 1

        self._tracks = [t for t in self._tracks if t.misses <= BOX_HOLD_FRAMES]
        self._tracks.extend(_Track(detections[i]) for i in unmatched_detections)

    @staticmethod
    def _closest_detection(box, detections, candidate_indices):
        tcx, tcy = box[0] + box[2] / 2, box[1] + box[3] / 2
        best_index, best_distance = None, None
        for i in candidate_indices:
            x, y, w, h = detections[i]
            dcx, dcy = x + w / 2, y + h / 2
            distance = ((tcx - dcx) ** 2 + (tcy - dcy) ** 2) ** 0.5
            if distance <= MAX_MATCH_DISTANCE and (best_distance is None or distance < best_distance):
                best_index, best_distance = i, distance
        return best_index

    @staticmethod
    def _smooth(previous_box, new_box):
        sx, sy, sw, sh = previous_box
        x, y, w, h = new_box
        return (
            int(SMOOTHING_ALPHA * x + (1 - SMOOTHING_ALPHA) * sx),
            int(SMOOTHING_ALPHA * y + (1 - SMOOTHING_ALPHA) * sy),
            int(SMOOTHING_ALPHA * w + (1 - SMOOTHING_ALPHA) * sw),
            int(SMOOTHING_ALPHA * h + (1 - SMOOTHING_ALPHA) * sh),
        )

    def _get_latest(self):
        with self._lock:
            frame = None if self._latest_frame is None else self._latest_frame.copy()
            boxes = list(self._latest_boxes)
        return frame, boxes

    def get_preview_frame(self):
        """Latest frame with a box drawn around every currently-tracked face,
        for the live display - or None if nothing's been captured yet."""
        frame, boxes = self._get_latest()
        if frame is None:
            return None
        for x, y, w, h in boxes:
            cv2.rectangle(frame, (x, y), (x + w, y + h), FACE_BOX_COLOR, 2)
        return frame

    def capture_jpeg(self):
        """The latest raw frame (no box overlay), downscaled if needed and
        JPEG-encoded, for sending to the backend for identification."""
        frame, _ = self._get_latest()
        if frame is None:
            raise RuntimeError("No frame available yet - is the webcam initializing?")
        frame = _cap_dimension(frame, MAX_UPLOAD_DIMENSION)
        ok, buffer = cv2.imencode(".jpg", frame)
        if not ok:
            raise RuntimeError("Failed to encode the captured frame as JPEG.")
        return buffer.tobytes()


def _cap_dimension(frame, max_dimension):
    height, width = frame.shape[:2]
    largest = max(height, width)
    if largest <= max_dimension:
        return frame
    scale = max_dimension / largest
    return cv2.resize(frame, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_AREA)
