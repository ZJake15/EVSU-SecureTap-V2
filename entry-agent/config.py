import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    api_base_url: str
    gate_location: str
    direction: str
    service_token: str
    camera_index: int
    offline_db_path: str
    poll_interval_seconds: float
    officer_name: str
    app_version: str
    camera_exposure: Optional[float]
    upload_max_dimension: int
    video_fps: int
    scan_pause_seconds: float
    student_display_fps: int


def _clamped(name, default, low, high, cast):
    try:
        value = cast(os.getenv(name, "").strip() or default)
    except ValueError:
        value = cast(default)
    return min(high, max(low, value))


def load_config() -> Config:
    raw_exposure = os.getenv("CAMERA_EXPOSURE", "").strip()
    return Config(
        api_base_url=os.getenv("API_BASE_URL", "http://localhost:8000/api"),
        gate_location=os.getenv("GATE_LOCATION", "Main Gate"),
        direction=os.getenv("DIRECTION", "entry"),
        service_token=os.getenv("SERVICE_TOKEN", ""),
        camera_index=int(os.getenv("CAMERA_INDEX", "0")),
        offline_db_path=os.getenv("OFFLINE_DB_PATH", "offline_queue.db"),
        poll_interval_seconds=float(os.getenv("POLL_INTERVAL_SECONDS", "0.5")),
        officer_name=os.getenv("OFFICER_NAME", "Guard on duty"),
        app_version=os.getenv("APP_VERSION", "v1.0"),
        # Unset (the default) leaves the webcam on its normal auto-exposure -
        # see camera.py for why this is opt-in/experimental rather than a
        # value this file guesses at.
        camera_exposure=float(raw_exposure) if raw_exposure else None,
        # Largest side (pixels) of each camera frame sent to the backend - see
        # camera.MAX_UPLOAD_DIMENSION. 960 by default; 640 on a low-power laptop.
        upload_max_dimension=int(os.getenv("UPLOAD_MAX_DIMENSION", "960")),
        # The next three are what the launcher's speed mode (Fast / Standard /
        # Light, chosen by the speed test) passes in - see device_setup.py at
        # the repo root. The defaults are Standard.
        # Gate monitor video smoothness, frames per second.
        video_fps=_clamped("VIDEO_FPS", 15, 5, 30, int),
        # Pause between one face check finishing and the next frame being
        # sent. Each check waits for the backend's answer, so a slow PC never
        # piles up frames; this pause is what leaves it CPU for the video.
        scan_pause_seconds=_clamped("SCAN_PAUSE_SECONDS", 0.2, 0.05, 2.0, float),
        # The Student Display's video smoothness, frames per second.
        student_display_fps=_clamped("STUDENT_DISPLAY_FPS", 10, 3, 30, int),
    )
