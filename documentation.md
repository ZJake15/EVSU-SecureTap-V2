# EVSU SecureTap — System Documentation

A face-recognition + NFC-card gate entry/exit system for EVSU. A camera watches the
gate continuously and identifies people by face (1:N, ArcFace embeddings); an NFC
card tap provides a second, independent verification channel and resolves cases
where the face match is ambiguous. Every event is logged, and a web dashboard gives
admin/security/IT staff live monitoring, user management, and reporting.

This document describes what the system does, how it's built, and the algorithms
behind face recognition. For setup/installation steps, see [README.md](README.md).

---

## 1. System overview

The system has three independent programs that talk to each other over HTTP:

```
┌─────────────────┐        ┌──────────────────────┐        ┌───────────────────┐
│   entry-agent     │  HTTP  │       backend          │  HTTP  │     dashboard        │
│ (guard's PC,       │◄──────►│ (Django REST API +    │◄──────►│ (React web app,       │
│  camera + NFC)     │        │  MySQL database)       │        │  browser)             │
└─────────────────┘        └──────────────────────┘        └───────────────────┘
```

- **entry-agent** — a Tkinter desktop app that runs at the physical gate. It owns the
  webcam and the NFC reader, but does **no machine learning itself** — it just
  captures frames/taps and sends them to the backend.
- **backend** — a Django REST API that owns the database, runs all face-recognition
  computation (embedding + matching), enforces roles/auth, and serves reports.
- **dashboard** — a React single-page app used by admin/security/IT staff to watch
  live activity, manage enrolled people, and view reports.

Only the backend touches machine learning or the database directly — the other two
are thin clients around its API.

---

## 2. Technology stack

| Component | Language | Framework / key libraries |
|---|---|---|
| **backend** | Python | Django 5.x, Django REST Framework, djangorestframework-simplejwt (JWT auth), django-filter, django-cors-headers, django-environ, MySQL (`mysqlclient`), **InsightFace** (ArcFace) on **ONNX Runtime**, OpenCV, NumPy, Pillow (+ `pillow-heif` for iPhone HEIC photos), pandas + openpyxl (bulk import), bcrypt |
| **dashboard** | JavaScript (React, JSX) | React 19, React Router 7, Axios, Recharts (charts), Tailwind CSS, `jwt-decode`, Vite (dev server/bundler) |
| **entry-agent** | Python | CustomTkinter (UI), OpenCV (webcam capture only, no ML), Pillow, `requests` (HTTP client), `python-dotenv`, `winsound` (Windows alert tone), SQLite (offline queue) |
| **Database** | — | MySQL 8.0+ |
| **Face recognition model** | — | InsightFace `buffalo_s` model pack (ArcFace recognition + RetinaFace-family detection), run via ONNX Runtime, CPU only |

No Node.js backend, no separate microservices, no message queue/Celery, no
WebSocket server — the dashboard and entry-agent both use plain HTTP polling
against the same Django REST API.

---

## 3. Does it use machine learning?

**Yes — for face recognition specifically, and nowhere else in the system.**
User management, reporting, and authentication are conventional CRUD/business logic
with no ML involved.

### 3.1 Model: InsightFace / ArcFace (`buffalo_s`)

- Uses the open-source **InsightFace** library, running the **`buffalo_s`** model
  pack via **ONNX Runtime** (CPU execution provider — no GPU required).
- `buffalo_s` bundles a detection model and an **ArcFace** recognition model
  (`w600k_mbf`, a MobileFaceNet backbone). The heavier `buffalo_l` pack
  (`w600k_r50`, ResNet-50) was deliberately **not** used — it benchmarked at
  ~900ms/face on the project's target hardware (a fanless, GPU-less laptop),
  too slow for a continuous ~5 frames/sec gate scan. `buffalo_s`'s lighter
  backbone trades a little accuracy for the speed a live CCTV-style scan needs.
- Only the `detection` and `recognition` sub-models are loaded
  (`allowed_modules=["detection", "recognition"]`) — the pack's bundled
  age/gender/landmark models are never used, saving CPU time per frame.
- Two separate `FaceAnalysis` instances are kept in memory: a full-size one
  (`det_size=640×640`) for enrollment photos (accuracy matters more than
  speed for a one-time, human-confirmed photo), and a smaller one
  (`det_size=480×480` by default, `GATE_SCAN_DET_SIZE`) dedicated to the
  continuous gate scan, trading some far-away-face detection range for
  faster per-frame turnaround.

### 3.2 What the model produces

Every detected face is converted into a **512-dimensional embedding vector**
(`face.normed_embedding`, L2-normalized) — a numeric fingerprint of that face in
ArcFace's learned embedding space. Two faces of the same person produce embeddings
that are close together in that space; different people produce embeddings that
are far apart. Raw photos are **not** compared pixel-by-pixel — only these vectors
are compared at match time.

### 3.3 Matching algorithm — cosine similarity

Because embeddings are unit-normalized, comparing two of them is a single dot
product:

```
similarity = embedding_A · embedding_B      # cosine similarity, range ~[-1, 1]
```

Higher = more similar (this is the opposite convention from the project's earlier
dlib-based version, which used Euclidean *distance*, where lower = more similar —
noted here because it's a common source of confusion in the code/config naming).

For the gate scan, one detected face is compared against **every enrolled active
person's stored embeddings in a single vectorized NumPy operation**:

```python
known_matrix   # shape (N, 512) — one row per stored embedding, across all enrolled people
similarities = known_matrix @ query_embedding   # cosine similarity vs. every embedding at once
best_index    = argmax(similarities)
```

A person can have **up to 5 embeddings** (from a guided multi-angle enrollment); a
match against *any* of their embeddings counts as a match for that person, using
their single best-scoring embedding.

> **Scale note:** this brute-force approach is fast for hundreds of embeddings but
> is explicitly not built to scale to a very large student body — a proper vector
> index (e.g. FAISS) would be the next step if enrollment grew much larger.

### 3.4 Decision thresholds (configurable, in `backend/.env`)

| Setting | Default | Meaning |
|---|---|---|
| `FACE_MATCH_SIMILARITY_THRESHOLD` | `0.45` | Minimum cosine similarity to count as a match at all. Explicitly documented as "a starting point, not a validated value" — see §3.6. |
| `TIEBREAK_MARGIN` | `0.05` | If the top match's score is within this margin of the threshold ("barely passed"), or the top-2 candidates are within this margin of each other (two people who look alike), the match is sent to NFC tiebreak instead of being auto-accepted/rejected. |
| `GATE_SCAN_MIN_BLUR_VARIANCE` | `25.0` | Below this Laplacian-variance blur score, a frame is skipped entirely (not counted as a match attempt in either direction) rather than trusted. |
| `FACE_EDGE_MARGIN_RATIO` | `0.02` | A face within 2% of the frame's edge is treated as likely partially cut off and skipped. |
| `GATE_SCAN_DET_SIZE` | `480` | Detector input resolution for the continuous scan (speed/range tradeoff). |

### 3.5 Multi-frame majority voting (anti-flicker / anti-false-positive)

A single video frame is **never** trusted enough to log a real entry/exit or flag
someone as "Unknown" on its own — a person has to be identified consistently across
several recent scan cycles first:

- Every ~0.2 seconds (`SCAN_INTERVAL_SECONDS`), the entry-agent sends the current
  frame to `/api/identify`.
- Each per-frame result is tallied as a `RecognitionAttempt` (for a match) or an
  `UnmatchedAttempt` (for a non-match), scoped to that specific gate.
- A match/non-match is only **confirmed** into a real, permanent `EntryLog` row
  once the same person (or "probably the same unrecognized face," grouped by
  embedding similarity) has been the top result in at least
  `VOTE_REQUIRED_AGREEMENT` (default **2**) of the last `VOTE_WINDOW_SIZE`
  (default **4**) attempts, within `VOTE_WINDOW_SECONDS` (default **4s**).
- Until confirmed, the entry-agent shows a neutral "Checking…" box rather than a
  green/amber verdict — so one bad frame mid-stride can't flag a real person as
  Unknown, and one lucky frame can't wrongly confirm a match.

### 3.6 Accuracy evaluation — FAR/FRR

A leave-one-out evaluation (`backend/users/threshold_eval.py`, exposed via
`manage.py evaluate_threshold` and the dashboard's Reports page) computes
**False Accept Rate / False Reject Rate** across a sweep of candidate similarity
thresholds, by comparing every enrolled person's photos against each other. This is
explicitly labeled a **preliminary estimate** in both the API response and the UI —
it's computed from currently-enrolled photos, not an independent held-out test set,
so it should not be read as a rigorous, unbiased accuracy claim.

### 3.7 Anti-spoofing note

There is currently **no liveness/anti-spoofing detection** — the system does not
try to distinguish a live face from a printed photo or a video played at the
camera. This is a known limitation worth addressing before any real, unsupervised
deployment (see §9).

---

## 4. Face-and-card interplay (why there's a second credential)

The NFC card is not just a shortcut — it's the system's tiebreaker and
manual-override channel:

1. **Clean face match** (similarity clearly above threshold, no close second
   candidate) → auto-confirmed after enough voting agreement, logged as
   `face_only`.
2. **Ambiguous face match** (borderline score, or two enrolled people who look
   similar) → the gate scan opens a `PendingTiebreak` for that gate and the
   entry-agent shows "Tap card to confirm." A card tap within
   `TIEBREAK_TIMEOUT_SECONDS` (default **10s**) resolves it and logs
   `face_and_card_tiebreak`. If nobody taps in time, it's logged as an
   unresolved/ambiguous failure.
3. **No face match** (confirmed via the same voting process) → logged as a failed
   `face_only` attempt; a cropped photo of the face and its embedding are saved so
   the same lingering stranger doesn't get logged repeatedly.
4. **Plain NFC tap / manual ID entry** (no camera match involved) → its own
   independent verification path, logged as `nfc_only`. Used for routine spot
   checks or when the camera isn't practical.

---

## 5. Data model

### `accounts` app
- **AdminProfile** — one-to-one extension of Django's built-in `User`
  (username/password, bcrypt-hashed). Fields: `role` (`admin` / `security` / `it`),
  `created_at`. This *is* the dashboard's entire roles/permissions system.

### `users` app
- **Person** — one record per enrolled student or staff member (no separate
  Student/Employee models). Fields: `full_name`, `role` (`student`/`staff`),
  `student_or_employee_id` (unique), `nfc_id` (unique), `department_or_course`
  (free text), `photo_reference` (display photo), `is_active` (soft-deactivate),
  `created_at`.
- **FaceEmbedding** — up to 5 per `Person`. Fields: `embedding` (512-d vector,
  JSON), `source_image`, `detection_score`, `is_low_confidence` (flags
  bulk-import/single-photo enrollments that skipped the guided quality gate),
  `created_at`.

### `logs` app
- **EntryLog** — the permanent audit record of every gate event. Fields: `person`
  (nullable FK — stays `Unknown` if the person is later deleted), `timestamp`,
  `direction` (`entry`/`exit`), `verification_method` (`nfc_only`, `face_only`,
  `manual_override`, `face_and_card_tiebreak`, plus a legacy `nfc_and_face` value
  kept only for old rows), `status` (`success`/`failed`), `gate_location` (free
  text), `failure_reason`, `captured_photo` (saved only for unrecognized faces),
  `unmatched_encoding` (embedding of an unrecognized face, for dedupe),
  `match_confidence` (cosine similarity at confirmation time).
- **RecognitionAttempt** / **UnmatchedAttempt** — short-lived, per-frame voting
  rows (see §3.5); pruneable, not permanent audit records.
- **PendingTiebreak** — at most one active "please tap your card" prompt per gate.

> Note: `gate_location` is a free-text string, not a separate Gate/Device model —
> multiple entry-agent instances pointed at different gate names show up as
> distinct gates in logs/reports, but there's no gate registry or per-gate
> settings UI.

---

## 6. Backend API

All endpoints are served under `/api/`. Two auth schemes are used side by side:
**JWT** (dashboard users, via `Authorization: Bearer <token>`) and a **shared
service token** (the entry-agent, via an `X-Service-Token` header) — the
entry-agent is treated as a trusted device, not a logged-in user.

| Endpoint | Method | Auth | Purpose |
|---|---|---|---|
| `/api/auth/login` | POST | open | JWT login; embeds role/username/full name in the token |
| `/api/auth/refresh` | POST | open | Refresh access token (rotated + blacklisted on rotation) |
| `/api/health` | GET | open | Liveness check |
| `/api/identify` | POST | service token | Continuous camera-frame face identification (§3) |
| `/api/verify` | POST | service token | NFC tap / manual ID lookup, tiebreak resolution |
| `/api/gate-summary` | GET | service token | Today's entries/exits/unknown counts for the entry-agent's stats strip |
| `/api/logs/live` | GET | JWT (any role) | "New logs since X" feed for Live Monitoring (polling, not push) |
| `/api/logs/` | GET | JWT (any role) | Paginated, filterable log history (read-only) |
| `/api/users/` | GET/POST/PUT/PATCH/DELETE | JWT (admin/IT) | Person CRUD; DELETE = soft-deactivate |
| `/api/users/{id}/permanent/` | DELETE | JWT (admin/IT) | Hard-delete a Person (logs keep "Unknown") |
| `/api/users/{id}/photos/` | POST | JWT (admin/IT) | Add another enrollment photo (up to 5) |
| `/api/users/bulk-import` | POST | JWT (admin/IT) | CSV/XLSX import matched against staged photos |
| `/api/users/check-photo-quality` | POST | JWT (admin/IT) | Stateless blur/face-count/face-size pre-check for guided enrollment |
| `/api/reports/summary` | GET | JWT (any role) | Daily/weekly counts, peak hour, by-method breakdown, confidence histogram, busiest hours |
| `/api/reports/far-frr` | GET | JWT (any role) | Preliminary FAR/FRR table (§3.6) |
| `/admin/` | — | Django superuser | Full Django admin panel |

Roles: **admin**, **security**, **it** — enforced by two permission classes,
`IsAdminOrIT` (user management) and `IsSecurityOrAbove` (read-only views). There is
no finer-grained per-object permission system.

---

## 7. Dashboard (web app) features

- **Login** — username/password, JWT-based session (auto-refresh on expiry, forced
  logout if refresh fails).
- **Live Monitoring** — polls every second; a live photo-grid feed of today's gate
  events with method badges (Face / Face+NFC tiebreak / Flagged), plus stat tiles
  (passes today, enrolled matches, unknown attempts, average confidence).
- **Logs** — paginated (25/page), filterable by date/name/gate/status, with a
  "hide unknown" toggle and a **client-side CSV export** (built in-browser from
  loaded rows; there's no server-generated export file).
- **User Management** *(admin/IT only)* — full Person CRUD; profile-photo upload
  via webcam capture or file; **guided 5-shot enrollment** (front/left/right/
  neutral/smile, each live quality-checked against `/api/users/check-photo-quality`)
  or a single-photo fallback (flagged low-confidence); add extra photos to an
  existing person; deactivate/reactivate/permanently delete; **bulk CSV/XLSX
  import** with per-row error reporting.
- **Reports** *(admin/IT only)* — range selector (today/7d/30d, up to 90d via API),
  stat cards, entries-by-method chart, confidence histogram, busiest-hours chart,
  entries-per-day chart, and the FAR/FRR curve with its "preliminary" caveat —
  built with Recharts.

There is currently no in-dashboard settings page, notification center, or
dashboard-account management UI (those admin accounts are created via a
management command or Django admin only).

---

## 8. entry-agent (gate device) features

Three windows, all built with CustomTkinter:

- **Launcher** — pick "Camera scanner" or "Card scanner" (both can be open at
  once); shows live NFC reader/camera/server status pills.
- **Camera scanner** — CCTV-style continuous monitoring, not a one-person kiosk;
  several faces in frame are each identified independently. Draws bounding
  boxes/names/confidence straight from the backend's `/api/identify` response
  (one source of truth — the entry-agent runs no local detector of its own).
  Green box = confirmed match, amber box + audible alarm + red banner = confirmed
  unknown person, blue box = "tap card to confirm" (ambiguous), gray box =
  "Checking…" (not yet confirmed). A live-log panel (4-column grid) shows recent
  events with photo, name, and an ENTRY/EXIT/UNKNOWN status badge.
- **Card scanner** — waits for an NFC tap (the reader emulates a USB keyboard; a
  hidden always-focused input field catches the typed card ID) or a manual
  student/employee ID entry as fallback. Shows a full result card (photo, name,
  role, ID, department, timestamp) on success or a specific failure reason
  (not registered / deactivated / read error) on rejection.

**Offline resilience**: NFC tap lookups that fail due to a network error are
queued in a local SQLite database (`offline_queue.db`) and automatically retried
every 15 seconds once connectivity returns. Camera-scan frames are **not** queued
offline — a stale frame from minutes ago isn't considered worth logging once back
online, so continuous scanning simply pauses/resumes with connectivity.

**Configuration** (`.env`): API URL, gate location, direction (entry/exit — fixed
per deployed instance), service token, camera index, officer name, optional manual
camera exposure. Each entry-agent instance is hard-configured to exactly one
camera and one fixed direction; a gate serving both directions currently needs two
separate instances.

---

## 9. Known limitations (documented in the codebase itself)

These are called out explicitly in code comments/docstrings, not bugs — worth
knowing before extending the system:

- **No anti-spoofing/liveness detection** — a printed photo or video held up to
  the camera is not currently defended against.
- `FACE_MATCH_SIMILARITY_THRESHOLD` (0.45 default) is an untuned starting point,
  not a value validated against a real deployment's enrolled population.
- Face matching is a brute-force vectorized NumPy scan — fine at hundreds of
  embeddings, not built to scale to a very large student body without a proper
  vector index.
- FAR/FRR reporting is a leave-one-out estimate over enrolled photos, not a
  rigorous held-out accuracy benchmark.
- `backend/media/` (reference/enrollment photos) is not access-controlled beyond
  Django's default file serving — flagged in the README's own privacy section.
- No rate limiting/throttling is configured on the API.
- No admin-action audit log (who edited/deleted a Person record) — only gate
  events are logged, not dashboard admin activity.
- No Celery/cron/task scheduler anywhere — periodic cleanup (e.g. expiring a
  stale tiebreak) piggybacks opportunistically on the next relevant request
  rather than running on a real schedule.
- NFC reader "ready" status in the entry-agent UI is hardcoded true — there's no
  reliable way to detect a HID-emulation reader's presence versus its absence.
- Only HID-keyboard-emulation NFC readers are supported; genuine PC/SC smart-card
  readers would need reintroducing the `pyscard` library.
- One entry-agent instance = one camera = one fixed direction; no multi-camera
  per gate, no automatic in/out direction detection.

---

## 10. Security & privacy notes

- Passwords are hashed with **bcrypt** (`BCryptSHA256PasswordHasher`, listed
  first in `PASSWORD_HASHERS`).
- Dashboard sessions use **JWT** access/refresh tokens (60-minute access token,
  12-hour refresh token, rotated and blacklisted on use).
- The entry-agent authenticates via a **shared secret header**
  (`X-Service-Token`), not a user login — appropriate for a trusted device, not a
  human session.
- Biometric data is stored as **512-d embedding vectors**, not raw face images,
  specifically to reduce biometric exposure — though reference/enrollment photos
  are still kept separately in `backend/media/`.
- The project's README includes an explicit **Philippine Data Privacy Act (RA
  10173)** notice: continuous camera scanning captures biometric data on everyone
  passing the gate, not just consenting enrollees (including visitors), and lists
  required mitigations before real deployment — written consent, a visible gate
  notice, a data-use/retention disclosure, a correction/deletion request process,
  and DPO sign-off.

---

## 11. Summary: what makes this system's approach distinctive

- **Continuous 1:N CCTV-style recognition**, not a stop-and-scan kiosk — multiple
  people in frame are each identified independently, every scan cycle.
- **Face and NFC are complementary, not redundant** — NFC specifically resolves
  ambiguous face matches (tiebreak) and serves as an independent fallback channel,
  rather than requiring both every time.
- **Multi-frame majority voting** on both the "match" and "no match" paths
  prevents a single bad frame from either wrongly confirming or wrongly flagging
  someone.
- **Deliberately thin edge device** — the entry-agent has zero ML dependencies;
  all recognition computation happens server-side, so the gate PC only needs to
  capture and display, not run a model.
