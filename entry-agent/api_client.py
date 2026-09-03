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

    def verify(self, gate_location, direction, nfc_id=None):
        """POSTs a card tap to /verify - a lookup only, no image involved.
        Raises requests.RequestException on any network failure so the
        caller can fall back to the offline queue."""
        data = {"gate_location": gate_location, "direction": direction}
        if nfc_id:
            data["nfc_id"] = nfc_id
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

    def identify(self, gate_location, direction, image_bytes):
        """POSTs a sampled camera frame to /identify for 1:N face matching.
        Raises requests.RequestException on any network failure."""
        response = self.session.post(
            f"{self.base_url}/identify",
            headers={"X-Service-Token": self.service_token},
            data={"gate_location": gate_location, "direction": direction},
            files={"image": ("frame.jpg", image_bytes, "image/jpeg")},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def fetch_photo(self, url):
        """Fetches a person's reference photo (returned by /verify) as raw bytes,
        to display on the tap-lookup result."""
        response = self.session.get(url, timeout=self.timeout)
        response.raise_for_status()
        return response.content
