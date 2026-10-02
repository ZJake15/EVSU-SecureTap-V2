import sqlite3
import sys
import threading
import traceback
from datetime import datetime, timezone

import requests


class OfflineQueue:
    """A tiny sqlite-backed queue for the NFC tap lookup flow specifically: if a
    tap can't reach the backend, it's stored here and retried by a background
    thread once connectivity returns, preserving the audit trail even though
    the guard couldn't see the photo live. (The continuous camera scan doesn't
    use this - a stale queued frame has no value once connectivity returns.)
    This is the offline-mode risk mitigation from the capstone proposal - a
    dropped connection should never stop entry logging outright."""

    # 4xx answers that mean "try again later", not "this tap is invalid":
    # a wrong/rotated service token (fixable in .env), a timeout, rate limiting.
    RETRYABLE_STATUSES = (401, 403, 408, 429)

    def __init__(self, db_path, api_client):
        self.db_path = db_path
        self.api_client = api_client
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._init_db()

    def _connect(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS pending_taps (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        nfc_id TEXT NOT NULL,
                        gate_location TEXT NOT NULL,
                        direction TEXT NOT NULL,
                        captured_at TEXT NOT NULL
                    )
                    """
                )
                conn.commit()
            finally:
                conn.close()

    def enqueue(self, nfc_id, gate_location, direction):
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT INTO pending_taps (nfc_id, gate_location, direction, captured_at) "
                    "VALUES (?, ?, ?, ?)",
                    (nfc_id, gate_location, direction, datetime.now(timezone.utc).isoformat()),
                )
                conn.commit()
            finally:
                conn.close()

    def pending_count(self):
        with self._lock:
            conn = self._connect()
            try:
                return conn.execute("SELECT COUNT(*) FROM pending_taps").fetchone()[0]
            finally:
                conn.close()

    def start_background_sync(self, interval_seconds=15):
        thread = threading.Thread(target=self._sync_loop, args=(interval_seconds,), daemon=True)
        thread.start()
        return thread

    def stop(self):
        self._stop_event.set()

    def _sync_loop(self, interval_seconds):
        while not self._stop_event.is_set():
            try:
                self._flush_once()
            except Exception:
                # A locked or unreadable queue file must not end this thread -
                # queued taps would then never sync for the rest of the run.
                traceback.print_exc()
            self._stop_event.wait(interval_seconds)

    def _flush_once(self):
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT id, nfc_id, gate_location, direction FROM pending_taps ORDER BY id"
                ).fetchall()
            finally:
                conn.close()

        # Network calls happen outside the lock so a slow/offline backend never
        # blocks enqueue() from the main polling loop.
        for row_id, nfc_id, gate_location, direction in rows:
            try:
                self.api_client.verify(gate_location, direction, nfc_id=nfc_id)
            except requests.exceptions.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status is not None and 400 <= status < 500 and status not in self.RETRYABLE_STATUSES:
                    # The backend got the tap and refused it for good (a
                    # malformed request) - retrying can never succeed, and
                    # leaving it at the head of the queue would block every
                    # tap queued after it forever.
                    print(f"offline queue: dropping tap {row_id}, rejected with HTTP {status}", file=sys.stderr)
                    self._delete(row_id)
                    continue
                break  # a server-side problem or a bad token - keep it, retry next interval
            except requests.RequestException:
                break  # still offline - stop this cycle, the next interval will retry
            else:
                self._delete(row_id)

    def _delete(self, row_id):
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM pending_taps WHERE id = ?", (row_id,))
                conn.commit()
            finally:
                conn.close()
