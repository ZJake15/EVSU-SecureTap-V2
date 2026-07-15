import os
from dataclasses import dataclass

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


def load_config() -> Config:
    return Config(
        api_base_url=os.getenv("API_BASE_URL", "http://localhost:8000/api"),
        gate_location=os.getenv("GATE_LOCATION", "Main Gate"),
        direction=os.getenv("DIRECTION", "entry"),
        service_token=os.getenv("SERVICE_TOKEN", ""),
        camera_index=int(os.getenv("CAMERA_INDEX", "0")),
        offline_db_path=os.getenv("OFFLINE_DB_PATH", "offline_queue.db"),
        poll_interval_seconds=float(os.getenv("POLL_INTERVAL_SECONDS", "0.5")),
    )
