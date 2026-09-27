# EVSU SecureTap — System Documentation

A face-recognition + NFC-card gate entry/exit system for EVSU. A camera watches the
gate continuously and identifies people by face (1:N, ArcFace embeddings), checks
that what it's looking at is a real live face (not a photo or screen), and an NFC
card tap provides a second, independent verification channel that also resolves
cases where the face match is ambiguous — including a specific, deliberate case no
camera can ever be expected to resolve on its own: two people (identical twins, or
any other genuine lookalikes) whose faces read as confusingly similar to the
matcher. Every event is logged, and a web dashboard gives role-scoped staff
(**Admin**, **SASO**, **Security Officer** — see §13) live monitoring, user
management, and reporting.

This document describes what the system does, how it's built, the step-by-step
process each scanner follows, and the algorithms behind face recognition and
liveness detection. For setup/installation steps, see [README.md](README.md).

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
  and liveness-detection computation, enforces roles/auth, and serves reports.
- **dashboard** — a React single-page app used by admin/security/IT staff to watch
  live activity, manage enrolled people, and view reports.

Only the backend touches machine learning or the database directly — the other two
are thin clients around its API.

---

## 2. How the scanners work — step by step

The entry-agent has two independent scanner windows, and they can both be open at
the same gate at once. This section walks through exactly what happens, in order,
for each one. See §5 for the algorithms/thresholds each step refers to.

### 2.1 Camera scanner (continuous face scan)

The camera scanner is CCTV-style — always watching, not a stop-and-pose kiosk.
Every cycle below repeats roughly every 0.2 seconds, independently for every face
currently in frame.

1. **Capture.** The entry-agent's `Camera` class keeps the webcam open continuously
   (DirectShow, up to 1280×720). Every `SCAN_INTERVAL_SECONDS` (0.2s) it grabs the
   latest frame, downsizes it to at most 960px on the long edge, and JPEG-encodes
   it — this happens whether or not anyone is actually standing at the gate.
2. **Upload.** The JPEG is POSTed to `/api/identify` along with the entry-agent's
   fixed `gate_location`/`direction` and its shared `X-Service-Token`. No identity
   is claimed — this is a "who, if anyone, is in this frame" request, not a lookup.
3. **Face detection.** The backend decodes the frame and runs InsightFace's
   scan-dedicated detector (`buffalo_s`, `det_size=480×480` by default) to find
   every face in the frame. Because InsightFace's `FaceAnalysis.get()` computes
   detection and recognition together, each detected face already has its 512-d
   ArcFace embedding at this point, regardless of what happens in later steps.
4. **Per-face quality gate.** Each face is checked for (a) sitting too close to the
   frame's edge (`FACE_EDGE_MARGIN_RATIO` — likely partially cut off), (b) being
   turned too far away from the camera (`FACE_MAX_YAW_RATIO`), and (c) motion blur
   below `GATE_SCAN_MIN_BLUR_VARIANCE`. Any one of them skips this face for this
   frame entirely — not counted toward a match *or* a non-match, just "still
   checking," so one bad frame mid-stride can't cost someone a vote either way.
   The entry-agent labels the box with a short hint for whichever check fired
   ("Move into view" / "Face the camera" / "Hold steady") instead of the generic
   "Checking…".

   **Why the frontality check matters:** a turned face is the one input that can
   fail in *both* directions at once. ArcFace embeddings are trained on roughly
   frontal faces, so a profile view of an enrolled person matches nobody and gets
   voted through as **Unknown**; the same odd angle can also drag the liveness
   score under its threshold and log them as a **spoof attempt** instead. Running
   the check before step 5 means neither can happen — the scan simply waits for
   the frame where the person looks at the camera, which at a normal walking pace
   is a fraction of a second later.

   Head pose is derived from the detector's own 5 keypoints (both eyes, nose tip,
   both mouth corners), so it costs nothing beyond the detection that already ran
   and needs no extra ONNX model loaded. The measure is how far the nose tip sits
   from the midpoint between the eyes, along the eye-to-eye axis, as a fraction of
   the inter-eye distance — ~0.0 dead-on frontal, ~0.35 about a 30° turn. Taking it
   along the eye axis rather than the image's x-axis makes it independent of head
   *tilt*: a head cocked sideways but still facing the camera scores ~0.0.
   Pitch (looking up/down) is deliberately **not** checked — with only 5 keypoints
   it can't be separated from the camera's mounting height, so a fixed pitch
   threshold would reject everyone at a high-mounted gate camera and nobody at a
   low one.
5. **Occlusion check.** A face that passes the quality gate is next checked for
   whether its mouth/nose read as covered (a hand, mask, or high collar) - before
   the liveness check and before ever being compared against anyone enrolled. If
   *any* of three independent signals fires (below), the face is routed to its own
   vote-then-confirm path (`OcclusionAttempt` → `IdentifyView._confirm_or_vote_occlusion`,
   the same `VOTE_REQUIRED_AGREEMENT`/`VOTE_WINDOW_SIZE`/`VOTE_WINDOW_SECONDS`
   grace period a match or spoof suspicion gets) instead of continuing to steps 6+.

   **Why this exists, and why it's a proxy, not a certainty:** an occluded face is
   the one input that fails *both* ways at once, the same problem the yaw check
   above solves for a turned face - ArcFace was never given a fair look at it, so
   matching it either produces a distorted embedding that matches nobody (voted
   through as **Unknown**, wrongly) or a real match forced on incomplete
   information (wrong the other way). Routing it to its own
   `EntryLog(status=occlusion_detected)` avoids both.

   InsightFace exposes **no per-landmark confidence or visibility score anywhere**
   in this installation - checked directly in the installed package source (the
   detector, `SCRFD`/`det_500m`, and the richer bundled-but-unloaded
   `landmark_2d_106`/`landmark_3d_68` models are both pure coordinate regression,
   neither with a per-point confidence output). An earlier iteration of this check
   also assumed a covered feature would produce a *geometrically implausible*
   landmark arrangement (eyes not level, nose not between/below them, mouth
   corners not symmetric) and planned to check that directly - **verified
   empirically, and it does not hold**: painting a skin-toned patch over the
   mouth/nose of real enrolled photos, every plausibility measure tried (eye
   level, eye-distance-to-box-size, nose offset from the eyes, mouth position/
   symmetry relative to the nose) came back statistically indistinguishable
   between clean and occluded faces. SCRFD's regression head doesn't produce a
   distorted-looking guess for a covered feature - it produces a *plausible but
   wrong* one, geometrically self-consistent with the rest of the face.
   Plausibility alone doesn't detect that. Three things that DO measurably differ
   are checked instead, and any one firing is enough:

   - **`mouth_visibility_ratio`** - mouth width (the two mouth-corner keypoints) as
     a fraction of inter-eye distance, reusing the same 5 keypoints the yaw check
     does. Below `FACE_MIN_MOUTH_VISIBILITY_RATIO` (default `0.73`). Verified
     empirically: painting a skin-toned patch over the mouth/nose of 10 real
     enrolled photos, clean ran 0.776-1.018 vs. 0.663-0.771 occluded - a real,
     measurable drop, but a far thinner margin than yaw's >10x separation.
   - **`lower_face_texture_ratio`** - added after real-world testing showed the
     ratio above, alone, badly under-detects **partial** coverage: a hand over
     just the mouth/chin (not reaching the nose) very often doesn't shrink mouth
     width enough to trip it, and that threshold can't be loosened further
     without misreading visible faces as covered (the clean-photo floor sits right
     against it). This check doesn't trust the regressed keypoint *positions* at
     all - only the face's bounding box (far more robust under occlusion than fine
     landmarks) - comparing Laplacian-variance texture (the same technique the
     blur check already uses) between the lower third of the face and the
     upper-middle: a real mouth is texture-rich (lips, teeth edges, the shadow
     under the nose), a covering hand or cloth is comparatively smooth. Below
     `FACE_MAX_MOUTH_TEXTURE_RATIO` (default `0.40`). On the same 10-photo test,
     several *specific* partial-coverage cases the mouth-ratio check missed
     entirely (0.735-0.783, still above 0.73) measured 0.166-0.328 here, comfortably
     below the clean floor (0.463) - a much cleaner separation, because the two
     signals degrade differently under the same occlusion.
   - **`det_score`** - the detector's own per-face detection confidence ("how
     face-like is this region", not a match score against anyone enrolled). Added
     because it fails independently of both ratios above: a partly covered face
     can still regress a plausible-looking keypoint arrangement (see the
     plausibility finding above) while still looking less face-like to the
     detector overall. Below `FACE_MIN_DET_SCORE_UNOCCLUDED` (default `0.65`).
     Measured on the same test: clean ran 0.71-0.84; occluded frames reached as
     low as 0.50-0.65, with real overlap into the clean range too - not clean
     separation on its own, which is why it's a third OR-condition, not a
     replacement for either ratio.

   Combined, the three caught 19 of 20 synthetic occlusion cases with zero false
   positives on any clean photo, versus roughly 14 of 20 for mouth-width alone -
   though on this exact synthetic test set `det_score` didn't move that number
   further (the one remaining miss stayed below its threshold too); it's included
   for real-world robustness against occlusion styles/lighting a flat synthetic
   patch doesn't reproduce, not because it improved this specific benchmark.

   All three remain proxies, not validated classifiers, and the combination still
   isn't airtight - the lightest possible partial coverage can miss all of them.
   See `insightface_utils.mouth_visibility_ratio`/`lower_face_texture_ratio` and
   each setting's own comment for the full numbers and known failure modes (a
   beard, heavy uneven lighting, or an unusually smooth-skinned mouth could
   plausibly read low on the texture check without anything actually covering
   it). Every default is set deliberately close to its clean-photo floor, biased
   toward **under-detecting** rather than misreading a visible face as covered -
   a missed occlusion just falls through to normal matching/Unknown handling,
   same as before any of this existed.

   A confirmed occlusion is deduped by **time alone** (`OCCLUSION_CAPTURE_
   COOLDOWN_SECONDS`), not by embedding similarity like an unmatched face or spoof
   attempt are - comparing embeddings would lean on the exact thing this feature
   doesn't trust. If a later frame in the same encounter (within `VOTE_WINDOW_
   SECONDS`) turns out unoccluded and matches or fails normally, that outcome is
   logged as usual (a clean match still logs `SUCCESS`) but carries
   `EntryLog.occlusion_detected=True` as a standing note that occlusion was seen
   moments earlier - kept on the record rather than silently dropped once resolved,
   since covering your face at a security gate and then uncovering it is itself
   worth keeping visible.

   **The Unknown/occlusion race (found from real production data, not assumed).**
   A continuous hand-over-face gesture produces a MIX of frames - a hand shifts
   slightly frame to frame, so some cross the occlusion thresholds above and some
   don't. Before a fix, those two outcomes voted in two fully independent buckets
   (`UnmatchedAttempt` for step 9's non-match vote, `OcclusionAttempt` here) that
   had no awareness of each other - both accumulating from the SAME physical
   event, racing each other, and "Unknown" won the race just as often as
   occlusion did. This was visible directly in real `EntryLog` rows during
   testing: `occlusion_detected` and `failed` alternating every few seconds for
   what was clearly one uninterrupted attempt to cover a face.

   The fix: `_confirm_or_vote_unmatched` now checks whether occlusion was seen at
   this gate within the last `VOTE_WINDOW_SECONDS` before ever counting a
   non-match toward the Unknown vote. If so, this frame doesn't compete in that
   vote at all - it's treated as more likely a continuation of the same
   occlusion event than a newly-arrived stranger, and the entry-agent shows the
   same "please uncover your face" box it would for a directly-detected
   occlusion frame, so the display stays one stable signal instead of flickering
   between the two. This gives the occlusion vote room to actually confirm,
   without loosening any of the three per-frame thresholds above (which stay
   exactly as calibrated). The tradeoff is bounded and deliberate: a genuine
   stranger who happens to walk up within that window of someone else's
   occlusion attempt has their own Unknown confirmation delayed by at most that
   window, not blocked - the same few-seconds-to-get-it-right philosophy this
   whole voting system already runs on everywhere else.
6. **Liveness check.** A face that passes the quality gate and the occlusion check
   is scored for liveness (§5.5) before it's ever compared against anyone
   enrolled. A face scoring below `LIVENESS_SCORE_THRESHOLD` (default `0.5`) is
   routed to step 7a instead of 7b.
7. **7a — Spoof voting** (low-liveness face). Tallied in `SpoofAttempt`, grouped by
   embedding similarity to recent low-liveness attempts at the same gate (there's
   no confirmed identity to key on yet). Only once enough recent attempts agree —
   `VOTE_REQUIRED_AGREEMENT` of the last `VOTE_WINDOW_SIZE`, within
   `VOTE_WINDOW_SECONDS`, the same voting settings used everywhere in this list —
   is it confirmed into a permanent `EntryLog(status=spoof_suspected)` row, with a
   cropped photo and the embedding saved so the same held-up photo/screen doesn't
   get logged again every cycle. The entry-agent's box turns **red immediately**
   on the first suspicious frame for guard awareness, even before it's confirmed —
   see step 10.
8. **7b — Identity matching** (live face). The face's embedding is compared, in one
   vectorized operation, against every active enrolled person's stored embeddings
   (up to 5 each — a guided enrollment captures several angles). The
   highest-scoring person and similarity win.
9. **Match decision:**
   - Below `FACE_MATCH_SIMILARITY_THRESHOLD` → not a match; goes through the same
     kind of multi-frame vote-then-log path as a spoof, via `UnmatchedAttempt` →
     eventually `EntryLog(status=failed)`.
   - **Top match is a person on record as confusable with someone else** (see
     §5.8) → checked *before* the two rules below, and overrides them outright: a
     `PendingTiebreak` opens **regardless of how high the similarity score is** —
     even a clean, unambiguous match gets sent to a card tap. This is the one
     case in this list that isn't about the score at all; it's a standing fact
     about the matched *person*, not a property of this particular frame.
   - Within `TIEBREAK_MARGIN` of the threshold ("barely passed"), or two enrolled
     people scoring within `TIEBREAK_MARGIN` of each other (they look alike) → too
     ambiguous to decide alone. A `PendingTiebreak` opens for the gate and the
     entry-agent asks for a confirming card tap (see §2.2, step 4, and §3).
   - Clearly above threshold with no close second → a real match candidate,
     tallied in `RecognitionAttempt` for voting.
10. **Multi-frame confirmation.** Matches, non-matches, spoof suspicions, *and*
    occlusion all go through the same debounce: nothing becomes a permanent
    `EntryLog` row (and nothing increments a stat tile or sounds the alarm) until
    it's been the consistent answer across enough recent frames. This is what
    stops one bad frame from wrongly flagging a real, enrolled person as Unknown,
    as a spoof, or as occluded, and stops one lucky frame from wrongly confirming
    a stranger as a match.
11. **Response & rendering.** The backend returns one result per detected face
    (box, verdict, similarity/liveness score, log id). The entry-agent draws a
    colored box per face straight from this response — **green** = matched,
    **amber** = confirmed unknown, **red** = spoof suspected (shown as soon as
    suspected, confirmed or not), **teal** = face covered (shown as soon as
    suspected, same as spoof), **blue** = tiebreak ("tap your card"), **gray** =
    still checking — appends a card to the live-log grid, updates the stats strip,
    and, for a newly *confirmed* unknown-person or spoof event specifically, plays
    an audible alarm and shows a banner over the video feed (occlusion shows the
    same banner, prominently, but without the alarm sound - covering your face
    isn't inherently adversarial the way a spoof attempt is).

### 2.2 Card scanner (NFC tap)

Unlike the camera scanner, a card tap is a deliberate, guard- or student-initiated
action, not a continuous background process.

1. **Input capture.** The NFC reader is HID-keyboard-emulation hardware — tapping a
   card "types" its ID followed by Enter into a hidden, always-focused text field
   in the gate monitor. A tap is the window's only typed input; there is no
   typed-ID fallback.
2. **Lookup request.** The entry-agent POSTs to `/api/verify` with `nfc_id`, plus
   the gate's fixed location/direction.
3. **Backend lookup.** The backend looks up a `Person` by that NFC ID. Not found →
   logged as a failed `nfc_only` attempt, reason "not registered." Found but
   `is_active=False` → logged as failed, reason "deactivated."
4. **Tiebreak check.** If there's currently a `PendingTiebreak` open for this gate
   (an ambiguous face match still waiting — see §2.1 step 8) and it hasn't expired
   (`TIEBREAK_TIMEOUT_SECONDS`, default 10s), this tap resolves *that* instead of
   being treated as an independent lookup — using the tiebreak's original
   direction, not a fresh `nfc_only` row. Any successful tap resolves it, whoever
   tapped — the tap itself, not a check against the tiebreak's candidate list, is
   what's trusted here. `PendingTiebreak.reason` decides which
   `verification_method` the resulting log gets: `face_and_card_tiebreak` for an
   ordinary ambiguous match, or `confusable_pair_tiebreak` (see §5.8) for one
   forced by a confusable-pair flag — kept as two distinct values on purpose, so
   the data can always answer "was this uncertain, or was it overridden on
   purpose."
5. **Cooldown dedupe.** A successful plain tap (not a tiebreak resolution) within
   `RECOGNITION_COOLDOWN_SECONDS` of the same person's last successful tap reuses
   that existing log row instead of creating a new one, so a guard tapping the
   same card twice in a row for a spot-check doesn't double-log an entry.
6. **Log & respond.** On success, a permanent `EntryLog(status=success)` row is
   written and the full match payload (photo, name, role, ID, department,
   direction, timestamp) is returned. On failure, a permanent
   `EntryLog(status=failed)` row is written too — a rejected card is still a
   security-relevant event worth keeping visible — with a specific reason code
   (`not_registered` / `deactivated` / `read_error`).
7. **Rendering.** The gate monitor's card panel shows the result (photo, name,
   role, course, ID, card ID, direction, timestamp) on success, so the guard can
   visually cross-check the tapped card against the person standing in front of
   them, or a red failure headline on rejection. Either way it auto-resets back
   to "Tap a card" after 8 seconds. The same outcome is simultaneously appended
   to the live log beside it (badge `CARD · ENTRY` / `CARD · EXIT` on success,
   `CARD ✗` on rejection), so a tap is as visible in the gate's running record as
   a face event.

---

## 3. Face-and-card interplay (why there's a second credential)

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
4. **Suspected spoof** (confirmed via the same voting process — see §5.5) → logged
   as `spoof_suspected`, distinct from a plain no-match, since this is a security
   event (someone actively trying to fool the camera), not just an unrecognized
   visitor.
5. **Plain NFC tap** (no camera match involved) → its own independent verification
   path, logged as `nfc_only`. Used for routine spot checks or when the camera
   isn't practical.
6. **Confusable-pair override** (the matched person is on record as unusually
   similar to someone else — identical twins, or any other pair the system can't
   be expected to tell apart visually) → **always** opens a tiebreak, even for an
   otherwise clean, unambiguous match. If nobody taps in time, this is the one
   case that does **not** fall back to a plain "unresolved/ambiguous" failure the
   way case 2 does — it's logged as needing manual guard/staff review instead,
   naming both candidates, since a genuine identity risk stayed unresolved, not
   just a low-confidence score. See §5.8 for the full reasoning.

---

## 4. Technology stack

| Component | Language | Framework / key libraries |
|---|---|---|
| **backend** | Python | Django 5.x, Django REST Framework, djangorestframework-simplejwt (JWT auth), django-filter, django-cors-headers, django-environ, MySQL (`mysqlclient`), **InsightFace** (ArcFace) + **MiniFASNetV2** on **ONNX Runtime**, OpenCV, NumPy, Pillow (+ `pillow-heif` for iPhone HEIC photos, registered in `users/apps.py`), pandas + openpyxl (bulk import), bcrypt |
| **dashboard** | JavaScript (React, JSX) | React 19, React Router 7, Axios, Recharts (charts), Tailwind CSS, `jwt-decode`, Vite (dev server/bundler) |
| **entry-agent** | Python | CustomTkinter (UI), OpenCV (webcam capture only, no ML), Pillow, `requests` (HTTP client), `python-dotenv`, `winsound` (Windows alert tone), SQLite (offline queue) |
| **Database** | — | MySQL 8.0+ |
| **Face recognition model** | — | InsightFace `buffalo_s` model pack (ArcFace recognition + RetinaFace-family detection), run via ONNX Runtime, CPU only |
| **Liveness/anti-spoofing model** | — | MiniFASNetV2 (Minivision AI, Silent-Face-Anti-Spoofing project), from-source ONNX export, run via the same ONNX Runtime, CPU only |

No Node.js backend, no separate microservices, no message queue/Celery, no
WebSocket server — the dashboard and entry-agent both use plain HTTP polling
against the same Django REST API.

> **Offline tooling, not part of the live pipeline:** `scikit-learn` and
> `joblib` are also installed on the backend, used only by the occlusion-
> classifier data-collection/training management commands described in §10 —
> not loaded or imported anywhere the running gate scan touches.

---

## 5. Does it use machine learning?

**Yes — two separate models, both for the camera scanner, and nowhere else in the
system.** User management, reporting, and authentication are conventional
CRUD/business logic with no ML involved.

### 5.1 Model 1 — identity: InsightFace / ArcFace (`buffalo_s`)

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

### 5.2 What the identity model produces

Every detected face is converted into a **512-dimensional embedding vector**
(`face.normed_embedding`, L2-normalized) — a numeric fingerprint of that face in
ArcFace's learned embedding space. Two faces of the same person produce embeddings
that are close together in that space; different people produce embeddings that
are far apart. Raw photos are **not** compared pixel-by-pixel — only these vectors
are compared at match time.

### 5.3 Matching algorithm — cosine similarity

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

### 5.4 Decision thresholds (configurable, in `backend/.env`)

| Setting | Default | Meaning |
|---|---|---|
| `FACE_MATCH_SIMILARITY_THRESHOLD` | `0.45` | Minimum cosine similarity to count as a match at all. Explicitly documented as "a starting point, not a validated value" — see §5.7. |
| `TIEBREAK_MARGIN` | `0.05` | If the top match's score is within this margin of the threshold ("barely passed"), or the top-2 candidates are within this margin of each other (two people who look alike), the match is sent to NFC tiebreak instead of being auto-accepted/rejected. |
| `GATE_SCAN_MIN_BLUR_VARIANCE` | `25.0` | Below this Laplacian-variance blur score, a frame is skipped entirely (not counted as a match attempt in either direction) rather than trusted. |
| `FACE_EDGE_MARGIN_RATIO` | `0.02` | A face within 2% of the frame's edge is treated as likely partially cut off and skipped. |
| `FACE_MAX_YAW_RATIO` | `0.35` | How far a face may be turned away from the camera before it's skipped rather than matched — nose-tip offset from the eye midpoint, over the inter-eye distance (≈30° turn). Set where ArcFace embeddings start degrading, so a turned enrolled person is never voted through as Unknown or as a spoof. See §2.1 step 4. |
| `FACE_MIN_MOUTH_VISIBILITY_RATIO` | `0.73` | First of two occlusion signals (either firing routes to `occlusion_detected` instead of Unknown/spoof) — mouth width over inter-eye distance, from the same 5 keypoints yaw uses. Alone, badly under-detects partial coverage (a hand over just the mouth/chin) — see the next row. See §2.1 step 5. |
| `FACE_MAX_MOUTH_TEXTURE_RATIO` | `0.40` | Second occlusion signal, added after real-world testing showed the one above missed too much: Laplacian-variance texture of the lower face vs. the upper face — a real mouth is texture-rich, a covering hand/cloth is comparatively smooth. Doesn't trust regressed keypoint positions, only the (more robust) bounding box. See §2.1 step 5. |
| `FACE_MIN_DET_SCORE_UNOCCLUDED` | `0.65` | Third occlusion signal: the detector's own per-face detection confidence (not a match score) — fails independently of the two ratios above, since a covered face can still regress a plausible-looking keypoint arrangement. See §2.1 step 5. |
| `OCCLUSION_CAPTURE_COOLDOWN_SECONDS` | `30` | Same "same situation still there" dedup as `SPOOF_CAPTURE_COOLDOWN_SECONDS`, for a confirmed `occlusion_detected` row — time-based only, since an occluded frame's embedding is exactly what this feature doesn't trust for a same-face comparison. |
| `GATE_SCAN_DET_SIZE` | `480` | Detector input resolution for the continuous scan (speed/range tradeoff). |
| `LIVENESS_SCORE_THRESHOLD` | `0.5` | Minimum combined liveness score (§5.5) to be treated as a real, live face. |

### 5.5 Model 2 — liveness/anti-spoofing

**Hardware constraint:** the gate camera is a standard 2D webcam — no depth or
infrared sensor — so a depth-map liveness check (the most robust approach against
photo/screen attacks) isn't possible on this hardware. Everything here is
"silent"/passive: a verdict from a single frame, no blink/head-turn challenge
prompt shown to the person at the gate.

Two independent signals are combined into one score, per detected face, after the
quality gate and before identity matching (§2.1, steps 5–7):

**Signal 1 — MiniFASNetV2 (primary, weight `0.65`).** A small trained CNN from the
Silent-Face-Anti-Spoofing project by Minivision AI (Apache-2.0), which classifies
an 80×80 face crop as live / print-attack / replay-attack. Chosen as the primary
signal because it's trained specifically on print/screen spoof data — the attack
this gate is most exposed to. Integrated as a **from-source ONNX export**, not a
downloaded pre-converted file:
1. The official `.pth` weights were downloaded and their SHA-256 verified against
   the officially documented hash.
2. The model architecture was reconstructed from the official source and the
   weights loaded with `strict=True` — zero missing/unexpected keys.
3. Exported to ONNX and checked for numerical parity against the original PyTorch
   model (max difference `1.4×10⁻⁶` across random inputs).
4. Verified end-to-end (real InsightFace detection → the same 2.7× context-crop
   the original model was trained on → this ONNX model) against the official
   repo's own labeled sample photos: a real face scored 99.97% live, two spoof
   photos scored 0.8% and 0.07% live.
5. Full provenance, hashes, and this verification trail live in
   `backend/users/liveness_models/NOTICE.md`.

The model expects a specific preprocessing: an 80×80 BGR crop taken not tightly
around the face but expanded 2.7× around its center (matching how the model was
trained), fed in as raw `[0, 255]` pixel values — **not** normalized to `[0, 1]`
(a real bug caught during integration: the official repo's own normalization step
is present in its source but commented out, so the model was actually trained on
un-normalized input).

**Signal 2 — classical texture/frequency/reflectance cues (secondary, weight
`0.35`).** Three peer-reviewed, non-deep-learning cues computed directly with
OpenCV/NumPy on the detected face crop — kept both as an always-on secondary
signal and as an automatic fallback (weighted `1.0`) if the ONNX model file is
ever missing or fails to load:
- **Micro-texture (Local Binary Patterns).** Real skin has fine-grained texture
  entropy a printout or screen surface doesn't reproduce.
  (Määttä, Hadid & Pietikäinen, IJCB 2011)
- **Frequency-domain analysis (2D FFT).** Printed halftone dots and screen
  pixel-grid moiré both add high-frequency artifacts a direct camera shot of a
  real face doesn't have.
  (Li, Wang, Cui et al., SPIE 2004)
- **Color/reflectance (HSV saturation + specular highlights).** Screens and glossy
  prints reflect light differently than skin.
  (Boulkenafet, Komulainen & Hadid, ICIP 2015)

**Combined score:**

```
liveness_score = 0.65 × MiniFASNet_P(live) + 0.35 × classical_score
```

compared against `LIVENESS_SCORE_THRESHOLD` the same "higher = more likely real"
way `FACE_MATCH_SIMILARITY_THRESHOLD` is used for identity. Measured latency: **~2.3ms per face**
(warm) — negligible next to the ~400–900ms ArcFace inference that already
dominates each gate-scan cycle.

**Why not MiniFASNet alone:** it's explicitly a single-frame method trained mainly
on print/screen attacks — it is not reliable against a 3D/silicone mask, and can
struggle against a very steady, high-quality video replay held right up to the
camera. The classical cues use independent signals with different failure modes,
so they're a genuine second layer, not redundancy — and they keep the feature
working even if the model file is unavailable.

**Note on the multi-frame vote (§2.1, step 9):** it applies identically to
liveness as it does to identity matches/non-matches, and is a general
noise-reduction mechanism, not a liveness signal in its own right — there is no
blink-detection or motion-consistency check in this system.

### 5.6 Multi-frame majority voting (anti-flicker / anti-false-positive)

A single video frame is **never** trusted enough to log a real entry/exit, flag
someone as "Unknown," or flag a spoof attempt on its own — the same result has to
come back consistently across several recent scan cycles first:

- Every ~0.2 seconds (`SCAN_INTERVAL_SECONDS`), the entry-agent sends the current
  frame to `/api/identify`.
- Each per-frame result is tallied as a `RecognitionAttempt` (match),
  `UnmatchedAttempt` (non-match), or `SpoofAttempt` (failed liveness), scoped to
  that specific gate.
- A result is only **confirmed** into a real, permanent `EntryLog` row once the
  same person (or "probably the same unrecognized/spoofed face," grouped by
  embedding similarity) has been the top result in at least
  `VOTE_REQUIRED_AGREEMENT` (default **2**) of the last `VOTE_WINDOW_SIZE`
  (default **4**) attempts, within `VOTE_WINDOW_SECONDS` (default **4s**).
- Until an unknown or spoof event is confirmed, the entry-agent doesn't log,
  count, or alarm on it (a confirmed match, and a suspected-but-unconfirmed
  spoof, still color their box immediately — see §2.1 step 10 — but neither
  writes a permanent record until confirmed).

### 5.7 Accuracy evaluation — FAR/FRR

A leave-one-out evaluation (`backend/users/threshold_eval.py`, exposed via
`manage.py evaluate_threshold` and the dashboard's Reports page) computes
**False Accept Rate / False Reject Rate** across a sweep of candidate similarity
thresholds, by comparing every enrolled person's photos against each other. This is
explicitly labeled a **preliminary estimate** in both the API response and the UI —
it's computed from currently-enrolled photos, not an independent held-out test set,
so it should not be read as a rigorous, unbiased accuracy claim. (This evaluation
currently covers identity matching only, not the liveness threshold — see §10.)

### 5.8 Confusable-pair detection (identical twins / lookalikes)

**The problem this solves, precisely.** Identical twins (or any two people with
unusually similar faces) can each score *high individual confidence* against their
own enrolled embeddings, while still being closer to each other's embeddings than a
genuine stranger would be. §5.4's ordinary tiebreak (`TIEBREAK_MARGIN`) only fires
when the top score is barely ahead of the second-best — it does **not** reliably
catch this case, because each twin can look confidently "themselves" without the
score *gap* ever looking small. Both can pass threshold cleanly, both can have a
comfortable lead over everyone else's embeddings — the ambiguity isn't in the gap,
it's in the two people being fundamentally hard for ArcFace to tell apart at all.

**Why this is a detection-and-route problem, not a matching-accuracy problem.**
ArcFace produces a similarity score from facial *geometry* — and identical twins
share that geometry by biological definition. No amount of threshold-tuning, more
enrollment photos, or better lighting closes that gap, because the thing being
measured is genuinely close to identical between the two people, not merely
*similar*. Treating this as an accuracy problem would mean either loosening
thresholds until unrelated strangers start getting accepted too, or tightening them
until the twins themselves start getting rejected — trading one failure mode for
another with no real improvement possible. The only correct fix is to stop trusting
the 2D face signal *at all* for this specific, known-hard case, and route it to a
second, independent factor (the NFC card, something the person physically
possesses) that doesn't share the twins' problem. That's the same justification
already used for liveness detection elsewhere in this system: don't try to make
face-matching detect what it structurally can't see — detect the case it can't
handle, in advance, and route around it.

**Detection, at enrollment.** Every time a new embedding is added to any enrolled
person — the first photo at creation, each of a guided capture's remaining photos,
an `add_photo` call, or a bulk-import row — it's compared against every *other*
active person's stored embeddings, the same vectorized cosine-similarity operation
§5.3 already does for gate-scan matching. Any cross-person similarity clearing
`CONFUSABLE_SIMILARITY_THRESHOLD` (default `0.60`) flags **both** records, not just
the new one, as a `ConfusablePair`. This is a deliberately separate, higher
threshold from `FACE_MATCH_SIMILARITY_THRESHOLD` — the match threshold answers "is
this good enough to accept as a match"; this one answers "is this so high that two
*different* people scoring it means their faces are fundamentally too alike for
this system to safely tell apart, no matter how any one match later turns out."

**The flag is persistent, not a one-time alert.** A twin pair doesn't stop being a
twin pair the day after enrollment — every future gate scan carries the same risk,
indefinitely. `ConfusablePair` is a standing database row (canonically ordered so
`(A, B)` and `(B, A)` can never both exist), checked on *every* gate-scan match
against the matched person, not a notification shown once and forgotten. An
enrolling Admin/SASO sees it as an ongoing "unusually similar to X — please confirm
this is expected (e.g. twins)" indicator on the person's record, not a one-time
popup — and can also manually flag (or remove) a pair by hand, for a mix-up
reported after the fact with no photo comparison involved (`source=manual` vs.
`auto_detected`, the latter also keeping the similarity score that triggered it).

**Enforcement, at the gate.** See §2.1 step 9 and §2.2 step 4: a match against a
flagged person always opens a tiebreak, regardless of score, and only a real tap
resolves it — an unresolved one is logged for manual review, naming both
candidates, never auto-accepted as whichever scored higher (attributing it to the
higher-scoring candidate would be exactly the false confidence this feature exists
to prevent).

**Manual backstop, not fed back into matching.** Two more things exist purely for a
human, never for the algorithm: a person flagged in a pair gets a higher
enrollment-photo ceiling (`MAX_EMBEDDINGS_PER_CONFUSABLE_PERSON`, default **8** vs.
the standard 5) so more of their actual variation is on file, and an optional free-
text `distinguishing_note` on their record (a visible mole, scar, glasses) that's
handed through to both the dashboard and the entry-agent's card-tap result panel
whenever a confusable-pair tiebreak resolves. Neither is used by face matching
itself — that's deliberate: the whole point is that the matcher can't see the thing
that actually tells the two people apart, so the system reaches a *person* instead.

| Setting | Default | Meaning |
|---|---|---|
| `CONFUSABLE_SIMILARITY_THRESHOLD` | `0.60` | Cross-person similarity above this flags both people as a confusable pair. Separate from, and higher than, `FACE_MATCH_SIMILARITY_THRESHOLD` — a starting point, not a validated value, same caveat as every other threshold on this page. |
| `MAX_EMBEDDINGS_PER_CONFUSABLE_PERSON` | `8` | Enrollment-photo ceiling once a person has an active confusable-pair flag (standard cap is 5) — confusable pairs should be rare, so the extra per-frame comparison cost stays negligible. |

---

## 6. Data model

### `accounts` app
- **AdminProfile** — one-to-one extension of Django's built-in `User`
  (username/password, bcrypt-hashed). Fields: `role` (`admin` / `saso` /
  `security_officer` — see §13), `assigned_gate_location` (free text, only
  meaningful for a `security_officer` — server-side gate-scoping reads this, never
  a client-supplied gate parameter, and a Security Officer with none set sees
  nothing, not everything), `created_at`. This *is* the dashboard's entire
  roles/permissions system.

### `users` app
- **Person** — one record per enrolled student or staff member (no separate
  Student/Employee models). Fields: `full_name`, `role` (`student`/`staff`),
  `student_or_employee_id` (unique), `nfc_id` (unique), `department_or_course`
  (free text), `photo_reference` (display photo), `is_active` (soft-deactivate),
  `distinguishing_note` (optional free text — see §5.8; never used by matching,
  only ever displayed to a human), `created_at`.
- **FaceEmbedding** — up to 5 per `Person` (8 once flagged in a confusable pair —
  see §5.8). Fields: `embedding` (512-d vector, JSON), `source_image`,
  `detection_score`, `is_low_confidence` (flags bulk-import/single-photo
  enrollments that skipped the guided quality gate), `created_at`.
- **DeactivationRequest** — the maker-checker state for a SASO-initiated
  deactivation (§13): a SASO can enroll/edit a `Person` but not deactivate one
  directly, so this stands in as "request, not an immediate change" until an
  Admin approves or rejects it. Fields: `person`, `requested_by`, `requested_at`,
  `reason` (required — what the approving Admin actually evaluates),
  `status` (`pending`/`approved`/`rejected`), `resolved_by`, `resolved_at`,
  `resolution_note`. A dedicated table rather than a flag on `Person`, so a
  rejected request followed by a fresh one later is naturally two rows, not one
  flag silently overwriting its own history.
- **ConfusablePair** — see §5.8. Fields: `person_a`/`person_b` (canonically
  ordered — lower id first — so `(A, B)` and `(B, A)` can never both exist as
  separate rows), `source` (`auto_detected`/`manual`), `detected_similarity`
  (null for a manually-flagged pair), `flagged_by` (null for an auto-detected
  one), `created_at`. Once flagged, a pair stays in force — there's no
  pending/dismissed workflow, only outright removal (Admin/SASO) — the cost of a
  false positive (one extra card tap) is deliberately far cheaper than the cost
  of a false negative (a lookalike waved through on face alone).

### `logs` app
- **EntryLog** — the permanent audit record of every gate event. Fields: `person`
  (nullable FK — stays `Unknown` if the person is later deleted), `timestamp`,
  `direction` (`entry`/`exit`), `verification_method` (`nfc_only`, `face_only`,
  `manual_override`, `face_and_card_tiebreak`, `confusable_pair_tiebreak` — see
  §5.8/§2.2 step 4 — plus a legacy `nfc_and_face` value kept only for old rows),
  `status` (`success` / `failed` / `spoof_suspected` / `occlusion_detected`),
  `gate_location` (free text), `failure_reason`, `captured_photo` (saved for
  unrecognized, spoof-suspected, *and* occlusion-detected faces),
  `unmatched_encoding` (embedding of an unrecognized or spoof-suspected face, for
  dedupe — deliberately **not** populated for an occlusion-detected row, since an
  occluded frame's embedding is exactly what §2.1 step 5 says not to trust for a
  same-face comparison), `match_confidence` (ArcFace cosine similarity at
  confirmation time), `liveness_score` (combined liveness score at confirmation
  time — populated on every face-scan row, matched or not), `occlusion_detected`
  (bool — separate from `status`: True if occlusion was seen at this gate moments
  before *this* row was written, regardless of how the row itself concluded; see
  §2.1 step 5's last paragraph), `performed_by` (FK to the dashboard `User` who
  logged this row by hand — set **only** on a `manual_override` row, tying it to
  a specific Security Officer's account; null on every automatic face/NFC row,
  since there's no dashboard account behind an automatic scan — see §13).
- **RecognitionAttempt** / **UnmatchedAttempt** / **SpoofAttempt** /
  **OcclusionAttempt** — short-lived, per-frame voting rows (see §5.6);
  pruneable, not permanent audit records. `OcclusionAttempt` is the simplest of
  the four: unlike Unmatched/Spoof it stores no embedding and does no
  similarity-based grouping, just a raw per-gate count within the vote window —
  grouping by an occluded frame's own embedding would lean on the thing this
  check doesn't trust.
- **PendingTiebreak** — at most one active "please tap your card" prompt per gate.
  Fields include `reason` (`ambiguous_match`/`confusable_pair` — see §2.1 step 9/
  §5.8), which decides the resulting `EntryLog.verification_method` once a tap
  resolves it, or expiry's log wording if none does.

### `audit` app
- **AuditLogEntry** — a single, generic permanent record of "who did what and
  when," for the specific state-changing actions the access-control design (§13)
  calls out as needing one: account created/updated/deactivated, deactivation
  requested/approved/rejected, manual override logged, person deleted
  permanently, confusable pair flagged/removed. Fields: `actor` (nullable FK —
  survives the actor's own account later being deleted, `SET_NULL`), `action`
  (the fixed set above), `target_description` (plain text, e.g. "Person #12 (Juan
  Dela Cruz)" — not a generic content-type FK, since the audited actions span a
  handful of unrelated models and a plain description reads directly in the UI
  with no extra joins), `detail` (optional JSON, action-specific — e.g. a
  deactivation's reason), `timestamp`. Deliberately **not** used for every read or
  routine action (viewing Reports, listing Person records) — only for the
  specific actions listed above; auditing every single edit would bury the ones
  that actually matter.

> Note: `gate_location` is a free-text string, not a separate Gate/Device model —
> multiple entry-agent instances pointed at different gate names show up as
> distinct gates in logs/reports, but there's no gate registry or per-gate
> settings UI.

---

## 7. Backend API

All endpoints are served under `/api/`. Two auth schemes are used side by side:
**JWT** (dashboard users, via `Authorization: Bearer <token>`) and a **shared
service token** (the entry-agent, via an `X-Service-Token` header) — the
entry-agent is treated as a trusted device, not a logged-in user.

| Endpoint | Method | Auth | Purpose |
|---|---|---|---|
| `/api/auth/login` | POST | open | JWT login; embeds role/username/full name in the token |
| `/api/auth/refresh` | POST | open | Refresh access token (rotated + blacklisted on rotation) |
| `/api/health` | GET | open | Liveness check (server up/down — unrelated to face liveness) |
| `/api/identify` | POST | service token | Continuous camera-frame face identification + liveness check (§2.1, §5) |
| `/api/verify` | POST | service token | NFC tap lookup, tiebreak resolution (§2.2) |
| `/api/gate-summary` | GET | service token | Today's entries/exits/unknown/spoof/occlusion counts for the entry-agent's stats strip |
| `/api/logs/live` | GET | JWT (any role) | "New logs since X" feed for Live Monitoring (polling, not push) — gate-scoped, today-only for Security Officer (§13) |
| `/api/logs/` | GET | JWT (any role) | Paginated, filterable log history (read-only) — same gate/today scoping for Security Officer |
| `/api/logs/manual-override` | POST | JWT (Security Officer only) | Logs a scanner-failure entry by hand after a visual ID check (§13); always `verification_method=manual_override`, `performed_by=`the caller |
| `/api/users/` | GET/POST/PUT/PATCH/DELETE | JWT (admin/SASO) | Person CRUD; DELETE/PATCH-to-inactive by an Admin = immediate soft-deactivate, by a SASO = creates a `DeactivationRequest` instead (202, not 204/200 — §13). `photo` and `profile_picture` must be JPEG, PNG or HEIC (§8) |
| `/api/users/{id}/permanent/` | DELETE | JWT (admin only) | Hard-delete a Person (logs keep "Unknown") |
| `/api/users/{id}/photos/` | POST | JWT (admin/SASO) | Add another enrollment photo (up to 5, or 8 if flagged confusable — §5.8); JPEG, PNG or HEIC only |
| `/api/users/bulk-import` | POST | JWT (admin/SASO) | CSV/XLSX import matched against staged photos (server-side files: `.jpg/.jpeg/.png/.heic/.heif`) |
| `/api/users/check-photo-quality` | POST | JWT (admin/SASO) | Stateless format + blur/face-count/face-size pre-check for guided enrollment |
| `/api/deactivation-requests/` | GET | JWT (admin/SASO) | Maker-checker queue — Admin sees all, SASO sees only their own (§13) |
| `/api/deactivation-requests/{id}/approve/` | POST | JWT (admin only) | Approves a pending request — deactivates the Person |
| `/api/deactivation-requests/{id}/reject/` | POST | JWT (admin only) | Rejects a pending request — Person stays active |
| `/api/confusable-pairs/` | GET/POST/DELETE | JWT (admin/SASO) | List, manually flag, or unflag a confusable pair (§5.8) |
| `/api/accounts/` | GET/POST/PUT/PATCH/DELETE | JWT (admin only) | Dashboard account (Admin/SASO/Security Officer) CRUD — Account Management |
| `/api/audit-log/` | GET | JWT (admin/SASO) | Admin sees every entry; SASO sees only entries where they're the actor (§13) |
| `/api/settings` | GET | JWT (admin only) | Read-only snapshot of every match/liveness/occlusion/voting threshold currently in effect (§5.4/§5.8) — editing still means changing `.env` and restarting, not a form on this page |
| `/api/reports/summary` | GET | JWT (admin/SASO) | Daily/weekly counts, peak hour, by-method breakdown, confidence histogram, busiest hours |
| `/api/reports/far-frr` | GET | JWT (admin/SASO) | Preliminary FAR/FRR table (§5.7) |
| `/admin/` | — | Django superuser | Full Django admin panel |

Roles: **admin**, **saso**, **security_officer** — see §13 for the full
permission matrix, the maker-checker deactivation flow, and gate-scoping. There is
no finer-grained per-object permission system beyond what §13 describes.

---

## 8. Dashboard (web app) features

- **Login** — username/password, JWT-based session (auto-refresh on expiry, forced
  logout if refresh fails).
- **Live Monitoring** — polls every second; a live photo-grid feed of today's gate
  events with method badges (Face / Face+NFC tiebreak / **Confusable pair + NFC**
  / Spoof suspected / Occlusion detected / Flagged / **Needs review — confusable
  pair**, for one that expired unresolved), plus stat tiles (passes today,
  enrolled matches, unknown attempts, spoof suspected, occlusion detected, average
  confidence). A card whose encounter briefly included a covered face before
  resolving into a match or non-match shows a small "face briefly covered
  earlier" note rather than silently dropping that fact; a confusable-pair
  tiebreak card shows the person's `distinguishing_note` (§5.8) if one's on file.
  Scoped to the caller's own gate, today only, for a Security Officer (§13) — the
  same server-side scoping `/api/logs/live` itself enforces, not a client-side
  filter.
- **Logs** — paginated (25/page), filterable by date/name/gate/status (including
  Spoof suspected and Occlusion detected), with a "hide unknown" toggle and a
  **client-side CSV export** (built in-browser from loaded rows; there's no
  server-generated export file — hidden entirely for a Security Officer, §13). The
  Reason column carries the same "face briefly covered earlier" note for a
  success/failed/spoof row whose encounter involved occlusion, distinct from the
  reason text an occlusion-detected row's own status already gives, plus the
  confusable pair's `distinguishing_note` on a `confusable_pair_tiebreak` row. A
  manual-override row (§13) shows a distinct "Manual override · `<username>`"
  badge instead of the usual verification method, so it's never mistaken for an
  automatic match. A Security Officer gets a manual-override entry form here
  instead of the export button/date filter, scoped to their own assigned gate.
- **User Management** *(admin/SASO — no access at all for Security Officer, §13)*
  — full Person CRUD; profile-photo upload via webcam capture or file; **guided
  5-shot enrollment** (front/left/right/neutral/smile, each live quality-checked
  against `/api/users/check-photo-quality`) or a single-photo fallback (flagged
  low-confidence); add extra photos to an existing person (up to 8 once flagged
  confusable — §5.8); an optional `distinguishing_note` field; **bulk CSV/XLSX
  import** with per-row error and confusable-pair-warning reporting. Deactivating
  a record is immediate for an Admin, but for a SASO opens a reason-prompted
  `DeactivationRequest` instead (§13) — an Admin sees a pending-requests queue
  here to approve/reject, and every record shows a "Pending deactivation" badge
  while one's outstanding. A separate **Confusable pairs** panel lists every
  flagged pair (auto-detected, with its similarity score, or manually flagged),
  a form to flag two existing people by hand, and a way to remove a pair flagged
  in error.

  **Photo uploads are restricted to JPEG, PNG and HEIC** (`.jpg`, `.jpeg`, `.png`,
  `.heic`, `.heif`). Every photo the dashboard can upload — the enrollment photo,
  each guided-capture slot, extra photos added to an existing person, and the
  separate profile picture — is checked, and anything else (WEBP, BMP, TIFF, GIF,
  **AVIF**) is rejected with "Only JPEG, PNG and HEIC photos are accepted - this
  file is WEBP."

  The backend decides on the file's **actual decoded format**, not its filename
  extension or the browser's `Content-Type`, so renaming `photo.webp` to
  `photo.heic` doesn't get it through. That distinction matters more than usual
  here: `pillow-heif` registers **AVIF** alongside HEIF, so AVIF is a format the
  server can now decode but deliberately won't accept — the allow-list is
  `("JPEG", "PNG", "HEIF")`, and AVIF reports as `"AVIF"`. The dashboard applies
  the same rule client-side purely so a wrong file is caught the moment it's
  picked; the server check is the one that matters. Webcam captures are unaffected
  — the browser encodes those as JPEG already.

  **A rejected file leaves no trace in the form.** Every photo file input is read
  through `readPhotoInput()`, which clears the input as it takes the file, so the
  browser stops displaying the chosen filename beside the button. A filename left
  sitting next to a red error message reads as though the upload succeeded, which
  is exactly backwards. Clearing unconditionally — rather than only on a
  client-detected rejection — also covers a file the *server* rejects, which the
  form can't predict at pick time, and it re-arms the input so picking the **same**
  file again after a failed quality check still fires a change event. Without that,
  a registrar retrying the same photo would get silence, because the input's value
  never changed. What's selected is communicated by the preview thumbnail (or the
  HEIC placeholder) and its Clear/Retake control, not by the native filename.

  **A note on how HEIC is named:** Pillow reports `"HEIF"` for a `.heic` file —
  HEIC is a specific packaging of HEIF, and `pillow-heif` maps `.heic`/`.heif`/
  `.hif` onto that single format string. The allow-list stores `"HEIF"`; the
  user-facing message says "HEIC", because that's what anyone with an iPhone
  calls it.

  **HEIC previews:** HEIC uploads are accepted, but no non-Apple browser can paint
  one, so the dashboard shows a labelled "HEIC — no preview" placeholder instead of
  a broken image (see `canPreviewInBrowser` in `dashboard/src/lib/photoUpload.js`).
  This is a display limitation only — the file uploads, is face-detected, and is
  re-encoded to JPEG by `normalize_to_jpeg` for storage, so the stored photo the
  dashboard renders afterwards is an ordinary JPEG.
- **Reports** *(admin/SASO — no access for Security Officer, §13)* — range
  selector (today/7d/30d, up to 90d via API), stat cards, entries-by-method chart
  (Face / Face+NFC / Failed / **Occluded**, its own stacked-bar segment rather
  than folded into Failed), confidence histogram, busiest-hours chart,
  entries-per-day chart, and the FAR/FRR curve with its "preliminary" caveat —
  built with Recharts.
- **Account Management** *(admin only)* — CRUD for dashboard logins (Admin/SASO/
  Security Officer), including each account's `assigned_gate_location` for a
  Security Officer. Deactivating a login here sets `is_active=False`, the same
  soft-deactivate `Person` uses, not a hard delete — an old audit entry attributed
  to that login should keep pointing at a real, if disabled, account.
- **Audit Log** *(admin/SASO)* — Admin sees every entry system-wide; a SASO sees
  only entries they themselves are the actor on. Security Officer has no access
  at all — not even their own, since the only thing they do that's audited
  (manual overrides) already shows up distinctly in Logs, not here. See §13.
- **System Settings** *(admin only)* — a read-only snapshot of every match/
  liveness/occlusion/voting/confusable-pair threshold currently in effect (§5.4/
  §5.8), read straight from `backend/.env`/`settings.py`. Explicitly not a live-
  editable form yet — changing a value still means editing that file and
  restarting the backend; the page says so rather than implying otherwise.

There is currently no notification center in the dashboard.

---

## 9. entry-agent (gate device) features

**One window**, built with CustomTkinter. The entry-agent has no menu of its
own: choosing "Entry Agent" in the system launcher (§9.1) is already the
decision, so it opens straight onto the scanners, the feed and the live log
rather than a second screen asking the same question again. The gate monitor
owns the Tk root, so closing it ends the process.

- **Gate monitor** — a single window owning both credentials at once, so a guard
  watches one screen rather than alt-tabbing between two. It opens maximized and
  is laid out by how much attention each panel deserves:
  - **Live log** (right, the widest panel) — a 4-column grid of recent events,
    each with photo, name, timestamp and a status badge. Both credentials land
    here: `ENTRY`/`EXIT` for a face match, `UNKNOWN`, `SPOOF`, and
    `CARD · ENTRY`/`CARD ✗` for an NFC tap — so the log is one chronological
    record of the gate regardless of how someone was identified.
  - **Live monitor** (top left, takes all the left column's spare height) —
    CCTV-style continuous monitoring, not a one-person kiosk; several faces in
    frame are each identified independently (§2.1). Draws bounding
    boxes/names/confidence straight from the backend's `/api/identify` response
    (one source of truth — the entry-agent runs no local detector of its own).
    Green box = confirmed match, amber box + audible alarm + red banner =
    confirmed unknown person, **red box = spoof suspected** (shown as soon as
    suspected, plus an audible alarm and banner once confirmed), **teal box =
    face covered** (shown as soon as suspected, same as spoof, plus a banner once
    confirmed — but no alarm sound, since covering your face isn't inherently
    adversarial the way a spoof attempt is; §2.1 step 5), blue box = "tap card to
    confirm" (ambiguous) or, for a confusable-pair-forced tiebreak specifically,
    "tap card — lookalike check" (§5.8) so it doesn't read as an ordinary
    uncertain match, gray box = "Checking…" (not yet confirmed). A matched
    or unmatched card whose encounter briefly included a covered face gets a small
    "face briefly covered" note on its own log card rather than losing that fact.
    **No camera connected** shows its own panel state — "No camera connected /
    Card taps still work — connecting a camera will resume automatically" —
    instead of a frozen or blank feed; the entry-agent starts up fine with no
    webcam at all (a gate can run on NFC taps and manual overrides alone), retries
    the connection on its own timer for as long as it runs, and picks a camera up
    automatically whether it's plugged in for the first time or was unplugged and
    reconnected mid-session — no restart needed either way.
  - **Card scanner** (bottom left, a compact strip) — waits for an NFC tap (the
    reader emulates a USB keyboard; a hidden always-focused input field catches
    the typed card ID) — a tap is the only input, there is no typed-ID fallback
    (§2.2). Shows photo, name, role, ID, card ID and timestamp on success, or a
    specific failure reason (not registered / deactivated / read error) on
    rejection, then resets to "Tap a card" after 8 seconds.
  - **Stats strip** (top) — Today / Entries / Unknown / Spoof / Occluded / In
    frame. Driven by camera events and seeded from `/api/gate-summary`; card taps
    show in the live log but don't move these counters.
  - **Status bar** (bottom) — recognition threshold, **Camera ✓/✗** next to a
    **camera-picker dropdown**, the officer name and app version on the left;
    Backend ✓/✗, offline-queue depth and sync state on the right. The camera chip
    and the officer/version line moved here from the entry-agent's old launcher
    screen when that screen was removed — a camera that has stopped responding is
    exactly what a guard needs to see, and a frozen feed doesn't always look
    different from an empty gate. The dropdown lists every camera Windows
    currently sees (by its real device name, via DirectShow enumeration — falling
    back to plain index-probing if that's unavailable) and switches the active
    feed within about 100ms of a selection — added because a laptop with a
    built-in webcam plus a USB camera plugged in for the gate otherwise showed
    "No camera connected" if `CAMERA_INDEX` in `.env` happened to point at the
    wrong one, with no way to fix it short of editing that file and restarting.

**Offline resilience**: NFC tap lookups that fail due to a network error are
queued in a local SQLite database (`offline_queue.db`) and automatically retried
every 15 seconds once connectivity returns. Camera-scan frames are **not** queued
offline — a stale frame from minutes ago isn't considered worth logging once back
online, so continuous scanning simply pauses/resumes with connectivity.

**Configuration** (`.env`): API URL, gate location, direction (entry/exit),
service token, camera index, officer name, optional manual camera exposure. These
are the instance's persistent defaults — gate location, direction, and officer
name can also be set per-launch from the system launcher's settings panel (§9.1)
without editing this file, which then passes them to this process as environment
variables (`GATE_LOCATION`/`DIRECTION`/`OFFICER_NAME`) that override the `.env`
values for that one run only. Camera is still chosen independently, via the
status-bar dropdown above, once the window is already open. Direction is still
fixed for the life of one running instance — a gate serving both directions
currently needs two separate entry-agent processes (one per direction) running at
once, not a way to flip a single running instance mid-shift.

### 9.1 System launcher (`launcher.py` / `SecureTap.bat`)

A single CustomTkinter window at the repo root, so running the system takes no
terminal commands at all. Double-clicking `SecureTap.bat` opens it.

It **starts the Django backend itself**, then offers the only choice that's
actually a choice — **Dashboard** or **Entry Agent**. The backend deliberately
isn't a third button: both front ends are useless without it, so presenting it as
an option would just be a step everyone has to perform every time.

- **Dashboard** — runs `npm run dev` in `dashboard/`, parses the URL Vite prints
  (Vite walks up a port when 5173 is taken, so the real one is read from its
  output rather than assumed), and opens the browser there.
- **Entry Agent** — spawns `entry-agent/main.py` as its own process, which opens
  **straight onto the gate monitor**: the feed, the card scanner and the live log,
  with no intermediate menu. Kept a separate process rather than imported, since
  it owns a camera, worker threads and its own Tk main loop.

Before opening the gate monitor, the launcher now also handles a few things a
guard would otherwise only discover once already standing at the gate:

- **Gate, direction, and guard name** are editable in a collapsed-by-default
  "Entry Agent settings" panel and remembered locally between launches
  (`launcher_settings.json`, a per-machine file, not something checked into the
  repo) — see the entry-agent's own **Configuration** paragraph above for exactly
  how these three values reach that process without editing `.env`.
- **Pre-flight checks** run right before the Entry Agent button actually spawns
  the process: whether the backend currently responds, and a best-effort check
  for an ACS ACR122U NFC reader specifically (the reader model this deployment
  uses) via Windows' own Plug-and-Play device list, matched against that reader's
  USB vendor/product ID. This is the only practical way to check for *that
  specific* reader — Windows sees any HID-emulation NFC reader as a generic
  keyboard, with no NFC-specific identity to query in general (see §10's note on
  why the gate monitor's own live "ready" indicator can't do this). Neither check
  blocks opening the gate monitor — a negative result shows as a one-time
  warning dialog, not a stop sign, since a live demo or a real shift can't afford
  to be blocked by a pre-flight check that's wrong.
- **Startup notices.** If the offline queue (see "Offline resilience" above) has
  any NFC taps still waiting to sync, or a record exists of the previous gate
  monitor session (written to `last_session.json` when that window closes, with
  its final entries/exits/unknown/spoof/occlusion counts, gate, direction, and
  when it ended), both show as a line on the launcher's main screen before
  anything is opened at all.
- **Auto-launch.** An optional setting in the same panel opens the gate monitor
  automatically a moment after the launcher itself starts, for a kiosk-style
  deployment where nobody should need to click anything.

The window is also responsive rather than fixed-size: its content is capped at a
comfortable reading width and centered instead of stretching edge-to-edge on a
large monitor, the two choice cards sit side-by-side above a width breakpoint and
stack below it, and the gate monitor's own stat-tile strip wraps to two rows
rather than squashing six tiles into one if its window is narrower than usual.
The current app version (the same value the gate monitor's status bar shows)
appears in the launcher's own footer too, so both windows always agree on it.

**Loading animation.** Both choices take several seconds before anything visible
happens — the entry-agent measured ~3.6–4.2s (importing `cv2`, opening the webcam
through DirectShow, building the window), and the dev server's first start is
comparable. The card's subtitle animates a pulse while that runs, so the wait
reads as work rather than as a button that missed the click.

It ends on a real signal, not a timer. `main.py` prints
`SECURETAP_ENTRY_AGENT_READY` on the line immediately before handing off to its
Tk loop; the launcher watches its output for that marker and stops the animation
when the window is genuinely up. The dashboard uses the URL Vite prints the same
way. Three ways out, so a spinner can never outlive what it's waiting for:
the ready marker, the process dying (the status poll notices and shows "Failed to
start"), or a 45-second timeout.

Design points worth knowing:

- **Adopts already-running services.** Before starting anything it probes
  `/api/health` and the dev-server port. A developer who already has `runserver`
  open in a terminal would otherwise get a second one that dies on "port already
  in use" — while the health probe kept answering from the *first* instance, so
  the launcher would show a green light beside a dead child. Adopted services are
  never killed on quit; only what the launcher started is.
- **Process trees, not processes.** Children are stopped with `taskkill /T`,
  because `npm` spawns `node` as a child and terminating `npm` alone would leave
  the dev server holding its port.
- **Output is captured, not discarded.** Each child's stdout+stderr is drained on
  its own thread into a rolling buffer behind a "Show log" panel. Draining isn't
  optional: an unread pipe fills at ~64KB and the child then blocks forever on its
  next write. The log is also where a backend that failed to start (MySQL down,
  bad `.env`) explains itself, instead of the button just appearing to do nothing.
- **Threading follows the same rule as the gate monitor** — output readers only
  touch plain data; every widget update happens on the Tk main thread.
- The design system (colors, `HeaderBar`, hover animation) is imported from
  `entry-agent/ui.py` rather than duplicated, so the launcher and the gate monitor
  can't drift into looking like two different products.

MySQL is out of scope — it's a Windows service, so the launcher reports the
backend's state but can't start the database.

---

## 10. Known limitations (documented in the codebase itself)

These are called out explicitly in code comments/docstrings, not bugs — worth
knowing before extending the system:

- **Liveness detection has real, known blind spots** — MiniFASNetV2 is a
  single-frame method trained mainly on print/screen attacks; it is not a defense
  against a 3D/silicone mask, and can struggle against a very steady, high-quality
  video replay. The classical texture/frequency/reflectance cues are heuristic,
  not learned, and their internal normalization ranges are unvalidated starting
  points (same caveat as the thresholds below).
- `LIVENESS_SCORE_THRESHOLD` (0.5 default), `FACE_MATCH_SIMILARITY_THRESHOLD`
  (0.45 default), and `CONFUSABLE_SIMILARITY_THRESHOLD` (0.60 default, §5.8) are
  all untuned starting points, not values validated against a real deployment's
  enrolled population or real spoof-attempt data.
- **Gate assignment for a Security Officer is a free-text string, not validated
  against a registry** — same limitation `gate_location` already has generally
  (see the note at the end of §6): a typo in `assigned_gate_location` silently
  mismatches against `EntryLog.gate_location` rather than erroring, since there's
  no Gate model to validate either one against.
- **System Settings (§8, §13) is read-only** — every threshold on the page is
  read straight from `settings.py`/`.env`; there's no live-editable config path
  yet. Doing that properly needs a DB-backed config table plus a cache-
  invalidation story for already-running worker processes — a meaningfully
  bigger change than the rest of the access-control work, so it's flagged as a
  follow-up rather than partially built.
- **Occlusion detection is three coarse proxies, not a validated classifier** —
  this project's detector (`buffalo_s`/SCRFD) exposes no per-landmark confidence
  or visibility score at all (checked directly in the installed package source,
  including the richer bundled-but-unloaded `landmark_2d_106`/`landmark_3d_68`
  models — both are pure coordinate regression too). A *geometric plausibility*
  check (eyes level, nose between/below them, mouth corners symmetric below the
  nose) was also tried and specifically **verified not to work** — SCRFD's
  regression head produces a plausible-but-wrong guess for a covered feature, not
  a distorted one, so plausibility measures came back statistically
  indistinguishable between clean and occluded faces in testing. Three things
  that DO measurably differ are combined instead (see §2.1 step 5):
  `FACE_MIN_MOUTH_VISIBILITY_RATIO` (mouth-width geometry),
  `FACE_MAX_MOUTH_TEXTURE_RATIO` (lower-vs-upper-face texture, added after
  real-world testing showed the geometry check alone missed most partial hand
  coverage), and `FACE_MIN_DET_SCORE_UNOCCLUDED` (the detector's own per-face
  confidence, which fails independently of both ratios). Measured on a 10-photo
  synthetic test, the three combined caught 19/20 occlusion cases with zero false
  positives on any clean photo — better than mouth-width alone (~14/20), but still
  not airtight; the single remaining miss was the lightest partial coverage
  tested, and the true real-world margin on any of the three is far thinner than
  the yaw check's. Expect continued misses on very light coverage (the safe
  failure mode: it just falls through to normal matching) and retuning against
  real occlusion data once any exists. None of the three attempts to detect
  occlusion of the *eyes* specifically (sunglasses) — only the mouth/nose region
  and overall face-likeness. **A trained classifier is the flagged upgrade path,
  and tooling for it now exists but isn't wired into live matching yet:**
  `manage.py collect_occlusion_training_data` captures labeled clean/degraded
  photo sets (straight-on, angled, and smiling for "clean"; hand over
  mouth/nose/eyes for "degraded") either live from a webcam or by harvesting
  existing enrollment photos, and `manage.py train_occlusion_classifier` fits a
  scikit-learn `RandomForestClassifier` on the same three measurable signals
  above (mouth-visibility ratio, lower-face texture ratio, detection score) —
  deliberately not on raw landmark geometry, since that's the exact
  plausible-but-wrong approach already found not to work, described above. Hand-
  over-eye photos are collected but excluded from training by default: none of
  the three current signals can detect eye coverage at all, so including them
  would just teach the classifier to guess. The command prints a side-by-side
  comparison against the current three-threshold rule on the same held-out
  photos and saves the trained model to a file — evaluation only, for now;
  nothing in the live gate scan reads that saved file yet.
- The FAR/FRR evaluation (§5.7) covers identity matching only — there is no
  equivalent held-out accuracy benchmark for the liveness threshold yet.
- Face matching is a brute-force vectorized NumPy scan — fine at hundreds of
  embeddings, not built to scale to a very large student body without a proper
  vector index.
- **Resolved** — `backend/media/` (reference/enrollment photos) used to be served
  with no access control at all whenever `DEBUG=True` (the previous default).
  Every media URL the API hands out is now a signed, time-limited link (see §11)
  instead of a bare, guessable path — closed as a security-hardening fix rather
  than left as an open item.
- No rate limiting/throttling is configured on the API.
- No admin-action audit log (who edited/deleted a Person record) — only gate
  events are logged, not dashboard admin activity.
- No Celery/cron/task scheduler anywhere — periodic cleanup (e.g. expiring a
  stale tiebreak) piggybacks opportunistically on the next relevant request
  rather than running on a real schedule.
- NFC reader "ready" status **inside the running gate monitor** is still
  hardcoded true — there's no reliable, general way to detect a HID-emulation
  reader's presence versus its absence while it's already running as a virtual
  keyboard. The system launcher now does a narrower, one-time version of this
  check *before* opening the gate monitor: a best-effort, model-specific search
  for an ACS ACR122U (this deployment's actual reader) in Windows' own connected-
  device list, by USB vendor/product ID (§9.1) — real for that one specific
  reader, but not a general "is any NFC reader plugged in" answer, and it only
  ever produces a one-time warning, never a live indicator that updates if the
  reader is unplugged mid-shift.
- Only HID-keyboard-emulation NFC readers are supported; genuine PC/SC smart-card
  readers would need reintroducing the `pyscard` library.
- One entry-agent instance = one camera = one fixed direction; no multi-camera
  per gate, no automatic in/out direction detection.

---

## 11. Security & privacy notes

- Passwords are hashed with **bcrypt** (`BCryptSHA256PasswordHasher`, listed
  first in `PASSWORD_HASHERS`), with a **10-character minimum length** (raised
  from an earlier default of 8 — Admin and SASO accounts hold real system power,
  so a short minimum was worth tightening for them specifically). This only
  affects passwords set or changed after the update; it doesn't invalidate an
  existing account's already-hashed password.
- Dashboard sessions use **JWT** access/refresh tokens (60-minute access token,
  12-hour refresh token, rotated and blacklisted on use).
- The entry-agent authenticates via a **shared secret header**
  (`X-Service-Token`), not a user login — appropriate for a trusted device, not a
  human session.
- Biometric data is stored as **512-d embedding vectors**, not raw face images,
  specifically to reduce biometric exposure — though reference/enrollment photos
  are still kept separately in `backend/media/`, now served only through
  **signed, time-limited URLs** rather than being openly downloadable. Every API
  response that includes a photo (enrollment photos, gate-capture photos, log
  thumbnails) signs that specific file's URL at the moment it's generated
  (`django.core.signing.TimestampSigner`, one-hour expiry) instead of returning a
  bare, guessable `/media/...` path — closing what the security review found to
  be the most serious issue in the whole system: with the previous open serving,
  anyone who could reach the server at all could download any enrolled person's
  photo by URL, no login required, whenever `DEBUG=True` (the default setting).
  The dashboard's `<img>` tags and the entry-agent's photo fetches both needed no
  code changes for this — a signed link is still just a link, it only carries an
  expiry and a check that nobody can forge without the server's own signing key.
- The MiniFASNetV2 model is a **from-source ONNX export with a verified chain of
  custody** (official Apache-2.0 weights, hash-checked, numerically verified
  against the original PyTorch model, end-to-end tested against labeled samples)
  rather than a downloaded pre-converted binary — see
  `backend/users/liveness_models/NOTICE.md`.
- The project's README includes an explicit **Philippine Data Privacy Act (RA
  10173)** notice: continuous camera scanning captures biometric data on everyone
  passing the gate, not just consenting enrollees (including visitors), and lists
  required mitigations before real deployment — written consent, a visible gate
  notice, a data-use/retention disclosure, a correction/deletion request process,
  and DPO sign-off.
- **Every dashboard endpoint enforces its role at the API level, not just the
  UI** (§13) — a Security Officer's token hitting `/api/users/` or
  `/api/reports/summary` directly gets a real 403, not just a hidden nav link.
  Deactivating a `Person` is a two-person action for a SASO (§13) — no single
  compromised or mistaken SASO account can unilaterally cut someone off. Every
  account change, deactivation request/resolution, manual override, and
  confusable-pair flag/removal is permanently attributed to a specific account
  in `AuditLogEntry` (§6), not just implied by who was logged in at the time.

---

## 12. Summary: what makes this system's approach distinctive

- **Continuous 1:N CCTV-style recognition**, not a stop-and-scan kiosk — multiple
  people in frame are each identified independently, every scan cycle.
- **Two-signal passive liveness detection** — a trained model (MiniFASNetV2) aimed
  at the most likely attack (print/screen) combined with independent classical
  cues as a second layer and an automatic fallback, entirely from a standard 2D
  webcam with no depth/IR hardware.
- **Face and NFC are complementary, not redundant** — NFC specifically resolves
  ambiguous face matches (tiebreak) and serves as an independent fallback channel,
  rather than requiring both every time.
- **Multi-frame majority voting** on the match, no-match, *and* spoof-suspected
  paths alike prevents a single bad frame from wrongly confirming, wrongly
  flagging, or wrongly clearing someone.
- **Deliberately thin edge device** — the entry-agent has zero ML dependencies;
  all recognition and liveness computation happens server-side, so the gate PC
  only needs to capture and display, not run a model.
- **Confusable pairs are detected in advance and routed, not "matched harder"**
  (§5.8) — identical twins are a case no threshold-tuning can ever fix, since the
  thing being measured (facial geometry) is genuinely near-identical between
  them; the system instead flags the pair once and permanently forces a second,
  independent factor for either of them, regardless of how confident any single
  match looks.
- **Role-based access control enforced server-side, with maker-checker on the
  one irreversible-feeling action** (§13) — three roles with a real permission
  matrix behind them, not just a hidden nav bar, and a SASO's deactivation
  request needs a second person (an Admin) to actually take effect.

---

## 13. Access control (roles & permissions)

Three dashboard roles, each with a real server-side permission class behind it
(`accounts/permissions.py`) — every endpoint in §7 enforces its own role there,
so hitting one directly with the wrong role's token returns a genuine 403, not
just a hidden button. There is no per-object permission system beyond this fixed,
role-level matrix.

| Area | Admin | SASO | Security Officer |
|---|---|---|---|
| Live Monitoring / Logs | Full — all gates, full history, export | Full — all gates, full history, export | **View-only**, own assigned gate, today only — no export, no date-range |
| User Management (enroll/edit) | Full | Full | **No access** (hidden from nav, blocked at the API) |
| Deactivate a Person | Immediate | Creates a `DeactivationRequest` instead (§13.1) | No access |
| Reports | Full (incl. FAR/FRR) | Full (incl. FAR/FRR) | **No access** |
| System Settings | Read-only view | No access | No access |
| Account Management | Full CRUD | No access | No access |
| Audit Log | Everything | **Own actions only** | No access |
| Manual override (§13.2) | No access (not their action to take) | No access | The only role that can |

### 13.1 Maker-checker deactivation

A SASO already has full enroll/edit power over `Person` records — day-to-day
enrollment is routine data entry. Deactivation is different: it's the one action
that can lock a real, legitimate person out of campus, and an *immediate*
deactivate has no safety net if a SASO account is ever compromised, or simply
makes a mistake under pressure. So a SASO's `DELETE` (or a `PATCH` setting
`is_active: false`) on `/api/users/{id}/` never takes effect directly — it
requires a `reason` and creates a `DeactivationRequest` (HTTP 202, not
204/200), sitting `pending` until a **different** person (an Admin) approves or
rejects it. Splitting "propose" from "execute" across two different accounts by
construction means a single actor — honest or not — can't unilaterally cut
someone off, and the required reason leaves a record of *why*, independent of
*whether* it happened. Reactivating (`is_active: true`) is left as an immediate
action for either role — undoing a deactivation isn't the irreversible,
security-sensitive direction maker-checker exists for. An Admin's own
deactivation is immediate, logged as `deactivation_approved` either way — an
Admin deactivating directly *is* the approval; there's no separate checker above
them in this system. Every request and its resolution is written to
`AuditLogEntry` (§6).

### 13.2 Manual override

A Security Officer operates the physical gate in real time and has no access to
User Management or Reports at all — their job is watching the scanner, not
managing records. When the scanner itself fails, they can log an entry by hand
after visually checking a physical ID: `POST /api/logs/manual-override` (§7),
Security-Officer-only. This always logs as `verification_method=manual_override`
with `performed_by` set to their own account (§6) — **never** merged into or
disguised as an automatic face-match success. The system's whole premise is that
entry decisions come from an unbiased biometric/NFC check; a manual override is
the opposite — a human's judgment substituting for it — which is powerful
precisely because it bypasses the thing the system is built to trust. Logging it
identically to a real match would misrepresent what actually happened at the
gate: there'd be no way to later ask "how often is the automated system actually
failing, and who approved entry when it did?" Tagging it distinctly, tied to a
specific account, keeps the log honest about whether a machine verified an entry
or a person vouched for it. Always creates a fresh `EntryLog` row too — never
deduped against a recent automatic success, since a manual override is never
"the same event" as an automatic one.

### 13.3 Gate scoping for Security Officer

`AdminProfile.assigned_gate_location` (§6) is read server-side
(`accounts/permissions.get_assigned_gate`) to filter every Live Monitoring/Logs
query for that role — never a client-supplied gate parameter, which a modified
request could otherwise use to see a different gate's activity. An unconfigured
Security Officer account (no gate assigned) sees **nothing**, not every gate —
failing closed on missing configuration rather than open.
