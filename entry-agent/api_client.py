import requests


class ApiClient:
    def __init__(self, base_url, service_token, timeout=10):
        self.base_url = base_url.rstrip("/")
        self.service_token = service_token
        self.timeout = timeout
        # A plain requests.post() opens a brand new TCP connection every
        # call; on this dev server that measured ~2s of pure connection
        # overhead per identify() call versus ~0.1s of actual server-side
        # work. A Session reuses one persistent (keep-alive) connection for
        # every call the continuous scan loop makes.
        self.session = requests.Session()

    def verify(self, gate_location, direction, nfc_id=None, replayed=False):
        """POSTs a card tap to /verify - a lookup only, no image involved.
        Raises requests.RequestException on any network failure so the
        caller can fall back to the offline queue. replayed: a tap the
        offline queue saved earlier (a staff card then signs nobody in)."""
        data = {"gate_location": gate_location, "direction": direction}
        if nfc_id:
            data["nfc_id"] = nfc_id
        if replayed:
            data["replayed"] = "true"
        response = self.session.post(
            f"{self.base_url}/verify",
            headers={"X-Service-Token": self.service_token},
            data=data,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def health(self):
        """GETs /health - unauthenticated, just answers "is the backend
        process up at all". Raises requests.RequestException if not."""
        response = self.session.get(f"{self.base_url}/health", timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def gate_summary(self, gate_location):
        """GETs today's Entries/Exits/Unknown counts for this gate, to seed
        the camera scanner's stats strip when it opens."""
        response = self.session.get(
            f"{self.base_url}/gate-summary",
            headers={"X-Service-Token": self.service_token},
            params={"gate_location": gate_location},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def gate_sign_in(self, gate_location, username, password):
        """A guard signing in for duty with their password. Returns who's on
        duty now; raises requests.HTTPError with the reason (wrong password,
        wrong gate, locked) in the response's "detail"."""
        response = self.session.post(
            f"{self.base_url}/gate/sign-in",
            headers={"X-Service-Token": self.service_token},
            data={"gate_location": gate_location, "username": username, "password": password},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def gate_sign_out(self, gate_location, shift_id=None, reason="signed_out", timeout=None):
        """Ends the shift at this gate (shift_id, or whatever is open).
        reason: signed_out, gate_closed or gate_reopened. Returns the new
        on-duty status."""
        data = {"gate_location": gate_location, "reason": reason}
        if shift_id:
            data["shift_id"] = shift_id
        response = self.session.post(
            f"{self.base_url}/gate/sign-out",
            headers={"X-Service-Token": self.service_token},
            data=data,
            timeout=timeout or self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def identify(self, gate_location, direction, image_bytes, camera_zoom=1.0, test_mode=False):
        """POSTs a sampled camera frame to /identify for 1:N face matching.
        camera_zoom tells the backend the frame is the middle 1/zoom of the
        camera's view, for its distance cutoff. test_mode (the capstone test,
        test_mode.py) marks what the frame writes as test entries - it never
        changes a decision. Raises requests.RequestException on any network
        failure."""
        data = {"gate_location": gate_location, "direction": direction, "camera_zoom": camera_zoom}
        if test_mode:
            data["test_mode"] = "1"
        response = self.session.post(
            f"{self.base_url}/identify",
            headers={"X-Service-Token": self.service_token},
            data=data,
            files={"image": ("frame.jpg", image_bytes, "image/jpeg")},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def test_snapshot(self, subject_id):
        """The test mode's settings snapshot: every Settings-page value, the
        backend's model setup, and the enrolled test subject (None if no one
        has that ID number)."""
        response = self.session.get(
            f"{self.base_url}/test-mode/snapshot",
            headers={"X-Service-Token": self.service_token},
            params={"subject_id": subject_id},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def delete_test_data(self):
        """Deletes every test entry and the scan's face data from the test
        sessions - see backend/logs/test_mode.py. Returns the counts."""
        response = self.session.post(
            f"{self.base_url}/test-mode/delete",
            headers={"X-Service-Token": self.service_token},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def fetch_photo(self, url):
        """Fetches a person's reference photo (returned by /verify) as raw bytes,
        to display on the tap-lookup result."""
        response = self.session.get(url, timeout=self.timeout)
        response.raise_for_status()
        return response.content
