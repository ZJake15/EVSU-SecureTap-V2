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
    )
