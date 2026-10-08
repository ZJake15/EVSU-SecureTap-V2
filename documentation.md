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
│  camera + NFC)     │        │  SQLite database)      │        │  browser)             │
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
5. **Occlusion check.** A face that passes the quality gate *and the liveness
   check* (step 6 — it runs first, so a photo or screen that also reads as
   covered is still caught and logged as a spoof rather than quietly prompted) is
   checked for whether its mouth/nose read as covered (a hand, mask, or high
   collar) - before ever being compared against anyone enrolled. If
   *any* of three independent signals fires (below), the face stops here instead
   of continuing to matching: the entry-agent is told to show "Please uncover
   your face" on it (`IdentifyView._occlusion_prompt`), and **nothing is logged** —
   no `EntryLog` row, no captured photo. Covering your face isn't inherently
   adversarial (a scarf, a cough, a phone call), so it's a prompt, not an event.
   Only a short-lived `OcclusionAttempt` marker is kept, for the two uses below.

   **Why this exists, and why it's a proxy, not a certainty:** an occluded face is
   the one input that fails *both* ways at once, the same problem the yaw check
   above solves for a turned face - ArcFace was never given a fair look at it, so
   matching it either produces a distorted embedding that matches nobody (voted
   through as **Unknown**, wrongly) or a real match forced on incomplete
   information (wrong the other way). Stopping at a prompt until the face is
   uncovered avoids both.

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

   **Trained classifier mode.** With `OCCLUSION_DETECTION_MODE=classifier` in
   `backend/.env`, the same three measurements go to a Random Forest trained on
   labeled clean/covered photos (`manage.py train_occlusion_classifier`, §5.9)
   instead of the three separate cutoffs — it weighs them together, and a face
   counts as covered when its "probably covered" probability reaches
   `OCCLUSION_CLASSIFIER_THRESHOLD`. It falls back to the three rules by itself
   if the model file is missing or won't load (for the whole process) or if a
   measurement couldn't be taken (for that frame), so switching it on can never
   stop the gate scan; the dashboard's Settings page shows which rule is actually
   in effect. See `users/occlusion_utils.py`.

   **A face the system already recognizes is never flagged as covered**, in
   either mode. Before the covered-face check runs, the face's embedding is
   compared against every enrolled person (`IdentifyView._already_recognizable`);
   if it already clears the normal match threshold, the covered-face check is
   skipped and the face goes on to liveness and the normal match vote. A
   genuinely covered face can't produce a confident match, so this only removes
   false alarms — and false alarms were the real problem in testing: the
   covered-face signals read a small or soft face (someone standing farther from
   the camera) much like a covered one, so an enrolled person with nothing on
   their face could be told to uncover it and never be recognized. It's a
   similarity check only, not a decision — the face still has to pass liveness,
   win the multi-frame vote, and go to a card tap if the match is borderline.

   If a later frame in the same encounter (within `VOTE_WINDOW_
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

   The fix: `_confirm_or_vote_unmatched` holds back the Unknown vote while
   covered frames **outnumber** unrecognized ones at this gate within the last
   `VOTE_WINDOW_SECONDS` (`_occlusion_dominates`) - it's treated as one
   covering gesture rather than a newly-arrived stranger, and the entry-agent
   shows the same "please uncover your face" box it would for a directly-detected
   covered frame, so the display stays one stable signal instead of flickering
   between the two. It takes a majority, not just any covered frame: covered
   faces aren't logged, and the covered-face check sometimes misreads an
   uncovered face (§10), so if one stray covered read were enough, an uncovered
   stranger could keep their own Unknown vote held off and never reach the log.
   Every non-match is still recorded while held, so once covered frames stop
   dominating the Unknown vote already has them, and the stranger is logged
   (with the alarm) within a frame or two.
6. **Liveness check.** A face that passes the quality gate is scored for
   liveness (§5.5) - before the occlusion check (step 5) and before it's ever
   compared against anyone enrolled. A face scoring below `LIVENESS_SCORE_THRESHOLD` (default `0.5`) is
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
    colored box per face straight from this response, each with a name tab
    (icon + words) on its top edge — **green** = matched (name · confidence),
    **amber** = confirmed unknown, **red** = spoof suspected (shown as soon as
    suspected, confirmed or not), **blue "PLEASE UNCOVER YOUR FACE"** = face
    covered (shown as soon as suspected, same as spoof), **blue "TAP CARD TO
    CONFIRM"** = tiebreak, **gray "Checking…"** = not yet decided. It then adds
    the event to the live log, updates the stats row, and, for a newly
    *confirmed* unknown-person or spoof event specifically, plays an audible
    alarm and docks an alarm banner across the top of the video panel
    (UNKNOWN PERSON / SPOOF SUSPECTED, with the time). A covered face gets
    neither the banner nor the alarm — covering your face isn't inherently
    adversarial the way a spoof attempt is, so the label on the face itself is
    the prompt, and the live view is never blocked for it. It is also **not
    logged or counted** — it never appears in the live log, the dashboard's
    logs or the reports (§2.1 step 5).

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
7. **Rendering.** The gate monitor's card-scanner strip shows the result (photo,
   name, role, course, ID, card ID, direction, timestamp) on success, so the
   guard can visually cross-check the tapped card against the person standing
   in front of them; on rejection it turns red with the failure headline ("Not
   registered", "Deactivated", ...) and `CARD REJECTED`; a tap queued while
   offline turns amber ("Offline — tap queued, will sync automatically"). The
   strip's left edge takes the same status color. Either way it auto-resets
   back to "Tap a card" after 8 seconds. The same outcome is simultaneously added
   to the live log beside it (`CARD · ENTRY` / `CARD · EXIT` on success,
   `CARD REJECTED` or `QUEUED` otherwise), so a tap is as visible in the gate's
   running record as a face event.

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
| **backend** | Python | Django 5.x, Django REST Framework, djangorestframework-simplejwt (JWT auth), django-filter, django-cors-headers, django-environ, WhiteNoise (serves the built dashboard), SQLite (built into Python; MySQL via `mysqlclient` optional), **InsightFace** (ArcFace) + **MiniFASNetV2** on **ONNX Runtime**, OpenCV, NumPy, Pillow (+ `pillow-heif` for iPhone HEIC photos, registered in `users/apps.py`), pandas + openpyxl (bulk import), bcrypt |
| **dashboard** | JavaScript (React, JSX) | React 19, React Router 7, Axios, Recharts (charts), Tailwind CSS, `@phosphor-icons/web` (icons), `jwt-decode`, Vite (bundler; builds `dashboard/dist`, which the backend serves at `http://localhost:8000/` — Node.js is only needed to build it, not to run it); fonts Archivo, Atkinson Hyperlegible Next and IBM Plex Mono from Google Fonts |
| **entry-agent** | Python | CustomTkinter (UI), OpenCV (webcam capture only, no ML), Pillow, `requests` (HTTP client), `python-dotenv`, `winsound` (Windows alert tone), SQLite (offline queue); bundled fonts in `entry-agent/assets/fonts/` (see §8.1) |
| **Database** | — | **SQLite** by default — one file (`backend/db.sqlite3`, or `DB_PATH`), nothing to install, WAL mode so the gate's frequent writes and the dashboard's reads don't block each other. **MySQL 8.0+** optional (`DB_ENGINE=mysql`), e.g. for a deployment with a separate database server; `manage.py copy_mysql_to_sqlite` moves existing data across |
| **Face recognition model** | — | InsightFace `buffalo_s` model pack (ArcFace recognition + RetinaFace-family detection), run via ONNX Runtime, CPU only |
| **Liveness/anti-spoofing model** | — | MiniFASNetV2 (Minivision AI, Silent-Face-Anti-Spoofing project), from-source ONNX export, run via the same ONNX Runtime, CPU only |

No Node.js backend, no separate microservices, no message queue/Celery, no
WebSocket server — the dashboard and entry-agent both use plain HTTP polling
against the same Django REST API.

> **Covered-face classifier:** `scikit-learn` and `joblib` are also installed
> on the backend. The occlusion-classifier data-collection/training commands
> (§10) use them to build the model, and the live gate scan loads that model
> with `joblib` when `OCCLUSION_DETECTION_MODE=classifier` is set (§5.9). In
> the default `rules` mode the live scan doesn't touch them.

---

## 5. Does it use machine learning?

**Yes — two pretrained models, plus one small model trained on this project's own
photos, all three for the camera scanner and nowhere else in the system:**
identity (§5.1), liveness/anti-spoofing (§5.5), and the covered-face classifier
(§5.9, used when `OCCLUSION_DETECTION_MODE=classifier`). User management,
reporting, and authentication are conventional CRUD/business logic with no ML
involved.

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

### 5.4 Decision thresholds (configurable from the Settings page)

These are now changed from the dashboard's **Settings** page (§8.2), which
stores them in the database. The `backend/.env` names below are only where
each value's *starting point* comes from: the first time the backend runs with
the Settings page, it copies the current `.env`/default value into the
database, so nothing changes on day one. After that the database wins — editing
`.env` for these values no longer has any effect.

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
| `OCCLUSION_DETECTION_MODE` | `rules` | Which rule decides "is this face covered": `rules` (the three thresholds above, any one tripping) or `classifier` (the trained Random Forest, same three measurements, falling back to the rules if the model can't be loaded). Either way a face that already matches an enrolled person is never flagged. The backend's dev server restarts itself when `backend/.env` or the model file changes, so a change or a retrain takes effect within a few seconds. See §2.1 step 5 and §5.9. |
| `OCCLUSION_CLASSIFIER_THRESHOLD` | `0.5` | Classifier mode only: the "probably covered" probability at or above which a face counts as covered. Raise it for fewer false "please uncover your face" prompts, lower it to catch more real coverings. |
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

### 5.9 Model 3 — covered-face classifier (Random Forest)

- **What it decides:** whether a detected face is covered (a hand, mask or cloth
  over the mouth/nose), so the person is asked to uncover rather than being
  matched on a distorted face or voted "Unknown" (§2.1 step 5).
- **Model:** a scikit-learn `RandomForestClassifier` — trained on this project's
  own labeled photos, not a downloaded pretrained model. It doesn't look at the
  image directly: its input is the same three measurements the original rules
  use — mouth-visibility ratio, lower-face texture ratio, and the detector's
  confidence — and it outputs a 0-1 "probably covered" probability. A face counts
  as covered at or above `OCCLUSION_CLASSIFIER_THRESHOLD` (default `0.5`).
- **Training data:** `manage.py collect_occlusion_training_data` captures labeled
  photos — "clean" (straight-on, slight left/right, slight smile) and "degraded"
  (hand over mouth, nose, mouth and nose, or an eye). `manage.py
  train_occlusion_classifier` trains on them (eye-covering photos excluded,
  since none of the three measurements can see the eyes), prints a held-out
  evaluation next to the original rules on the same photos, and saves the model
  to `backend/users/occlusion_classifier.joblib`. The model file isn't committed
  to the repo — each installation trains its own.
- **Current accuracy** (124 clean / 186 covered photos, measured on the training
  photos themselves, so real-world numbers are likely lower): catches about 86%
  of hand-over-mouth, 89% of hand-over-mouth-and-nose and 90% of hand-over-nose
  photos, versus about 30% for the original rules; flags 19 of the 124 clean
  photos (15%) as covered. See §10 for what those false alarms mean in practice.
- **Switching it on:** `OCCLUSION_DETECTION_MODE=classifier` in `backend/.env`.
  The model is loaded once per backend process; if the file is missing or
  unreadable it logs a warning and uses the original rules instead, so it can
  never stop the gate scan. The dashboard's Settings page shows which rule is
  actually in effect.
- **Always paired with the recognition check:** in either mode, a face that
  already matches an enrolled person is never flagged as covered
  (`_already_recognizable`, §2.1 step 5) — this is what keeps the classifier's
  false alarms away from enrolled people.
- **Reloading:** switching modes on the Settings page takes effect within a few
  seconds. The model file is loaded once, so after retraining, restart the
  backend (the launcher runs it without the auto-restart helper, §14).

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
  `status` (`success` / `failed` / `spoof_suspected`, plus `occlusion_detected`,
  kept only so rows from before covered faces stopped being logged still
  display — no new ones are written, §2.1 step 5),
  `gate_location` (free text), `failure_reason`, `captured_photo` (saved for
  unrecognized and spoof-suspected faces),
  `unmatched_encoding` (embedding of an unrecognized or spoof-suspected face, for
  dedupe), `match_confidence` (ArcFace cosine similarity at
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
| `/api/settings` | GET, PATCH | JWT (admin only — 403 for other roles) | Every setting with its value, limits and wording; PATCH `{"values": {...}, "confirmed": bool}` saves all-or-nothing (400 with per-setting errors, 409 if a risky change wasn't confirmed). See §8.2 |
| `/api/session-policy` | GET | JWT (any role) | The two settings every page needs: inactivity-logout minutes (0 = off) and whether single-photo registration is allowed |
| `/api/reports/summary` | GET | JWT (admin/SASO) | Daily/weekly counts, peak hour, by-method breakdown, confidence histogram, busiest hours |
| `/api/reports/far-frr` | GET | JWT (admin/SASO) | Preliminary FAR/FRR table (§5.7) |
| `/admin/` | — | Django superuser | Full Django admin panel |

Roles: **admin**, **saso**, **security_officer** — see §13 for the full
permission matrix, the maker-checker deactivation flow, and gate-scoping. There is
no finer-grained per-object permission system beyond what §13 describes.

---

## 8. Dashboard (web app) features

Every page shares one layout: a maroon left sidebar (EVSU seal and wordmark,
the pages this role can open, the signed-in account and Log out) and, on the
right, a page header — a small condensed eyebrow line above a large title — with
the page's main action beside it. See §8.1 for the visual design system.

- **Login** — username/password, JWT-based session (auto-refresh on expiry, forced
  logout if refresh fails). A split screen: the EVSU seal and wordmark on a maroon
  panel, the sign-in form beside it, with an inline error notice on a failed
  sign-in.
- **Live Monitoring** — polls every second. Across the top, one large "passes
  today" number followed by enrolled matches, unknown attempts, spoof suspected,
  face covered and average confidence (a count only takes its status color once
  it's non-zero). Below, two columns: the **latest pass** as a large card (photo,
  name, ID, time, method, confidence), then a table of today's successful passes
  (name, ID, time, method — Face / Face + card / Lookalike + card / Manual /
  Card — confidence, gate); and a **Needs attention** column pinning every
  non-routine event as its own card with a colored left edge and a word —
  `SPOOF SUSPECTED`, `FACE COVERED`, `FLAGGED · UNKNOWN`, or `NEEDS REVIEW` for a
  confusable-pair tiebreak that expired unresolved. A pass whose encounter
  briefly included a covered face shows a small "face briefly covered earlier"
  note rather than silently dropping that fact; a confusable-pair tiebreak shows
  the person's `distinguishing_note` (§5.8) if one's on file. There's an empty
  state ("Waiting for the first scan of the day") and a reconnecting notice if
  the feed drops. Scoped to the caller's own gate, today only, for a Security
  Officer (§13) — the same server-side scoping `/api/logs/live` itself enforces,
  not a client-side filter.
- **Logs** — paginated (25/page), filterable by date/name/gate/status (including
  Spoof suspected and Face covered), with a "hide unknown" toggle and a
  **client-side CSV export** (built in-browser from loaded rows; there's no
  server-generated export file — hidden entirely for a Security Officer, §13).
  Status is an icon + word in its status color, and every non-routine row gets a
  colored left edge (red for failed/spoof, blue for face covered, brass for a
  manual override). The Reason column carries the same "face briefly covered
  earlier" note for a success/failed/spoof row whose encounter involved
  occlusion, distinct from the reason text an occlusion-detected row's own status
  already gives, plus the confusable pair's `distinguishing_note` on a
  `confusable_pair_tiebreak` row. A manual-override row (§13) shows "Manual
  override · `<username>`" as its method instead of the usual verification
  method, so it's never mistaken for an automatic match. A Security Officer gets
  a **Manual override** panel beside the table instead of the export
  button/date filter, scoped to their own assigned gate.
- **User Management** *(admin/SASO — no access at all for Security Officer, §13)*
  — organized as three tabs: **People** (the searchable, filterable list — name,
  role, ID, NFC ID, department, status — with a one-line flag under a name for
  "Pending deactivation", "Lookalike pair" or "Single-photo enrollment · lower
  confidence"), **Requests & pairs** (the Admin's pending-deactivation queue
  beside the confusable pairs), and **Bulk import**. Adding or editing a person
  happens in a **drawer** that slides in from the right, so the list stays in
  view. Full Person CRUD; profile-photo upload via webcam capture or file;
  **guided 5-shot enrollment** (front/left/right/neutral/smile, each live
  quality-checked against `/api/users/check-photo-quality`, shown as five tiles
  that turn green/red as each shot passes or fails; until a shot is taken, its
  tile shows an outline drawing of that pose — straight on, head turned slightly
  left or right, no expression, smiling — and the shot being captured shows its
  drawing larger beside the camera for the person to copy) or a single-photo fallback
  (flagged low-confidence); add extra photos to an existing person (up to 8 once
  flagged confusable — §5.8); an optional `distinguishing_note` field; **bulk
  CSV/XLSX import** with per-row error and confusable-pair-warning reporting.
  Deactivating a record is immediate for an Admin, but for a SASO opens a
  reason-prompted `DeactivationRequest` instead (§13) — an Admin approves or
  rejects each one from its own card on the Requests & pairs tab, optionally
  typing a note to the requester there before rejecting, and every record shows
  a "Pending deactivation" flag while one's outstanding. The **Lookalike pairs**
  list shows every flagged pair (auto-detected, with its similarity score, or
  manually flagged), a form to flag two existing people by hand, and a way to
  remove a pair flagged in error.

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
- **Reports** *(admin/SASO — no access for Security Officer, §13)* — a
  Today / Last 7 days / Last 30 days toggle (up to 90d via API); entries today
  as the large number, beside failed verifications and the busiest hour; an
  entries-by-method bar (Face / Face + card / Failed / **Face covered**, its own
  segment rather than folded into Failed) with CSV export; and four charts —
  busiest hours (peak hour highlighted), entries per day for the last week
  (today highlighted), the confidence histogram, and the FAR/FRR curve with its
  "preliminary estimate" caveat and, for an Admin, a marker at the match
  threshold currently in effect — built with Recharts.
- **Account Management** *(admin only)* — the list of dashboard logins (Admin/
  SASO/Security Officer) beside an edit panel; clicking a row opens it for
  editing (username, first/last name, password, role, and each Security
  Officer's `assigned_gate_location`), and "New account" clears the panel for a
  new one. Deactivating a login (from the edit panel) sets `is_active=False`,
  the same soft-deactivate `Person` uses, not a hard delete — an old audit entry
  attributed to that login should keep pointing at a real, if disabled, account.
- **Audit Log** *(admin/SASO)* — Admin sees every entry system-wide; a SASO sees
  only entries they themselves are the actor on. Security Officer has no access
  at all — not even their own, since the only thing they do that's audited
  (manual overrides) already shows up distinctly in Logs, not here. See §13.
  Filterable by actor and action; each action shows with its own icon, a
  permanent deletion in red, and a manual override with a brass left edge.
- **Settings** *(Admin only — SASO and Security Officer get a 403 from the
  server)* — every setting that controls the system, in nine sections plus
  Advanced, each with a plain-English description, changeable from the page
  and in effect at every gate within a few seconds. See §8.2.

There is currently no notification center in the dashboard.

### 8.2 Settings page — how it works

**What it is.** One page where an Admin can see and change every value that
controls how the gates behave — how strict face matching is, when to ask for a
card, how long records are kept, and so on. A change takes effect at every gate
within a few seconds; nobody has to restart the backend or the gate monitor.

**Where the values live.** In a database table, `configuration_systemsetting`,
one row per setting. Every setting is also described once in code
(`backend/configuration/registry.py`): what kind of value it is (a number, an
on/off switch or a choice), the lowest and highest value allowed, the
recommended range for the risky ones, its label and its plain-English
description. The page draws itself from that list, so the wording on screen and
the rules on the server can never disagree.

**Day one changes nothing.** The first time the backend needs a setting, it
fills its row with the value the system already used (from `backend/.env` or
the code's default). New features start **off**: automatic deletion, failed
login lockout, automatic logout, the ask-for-card range and the repeated-unknown
alert all do nothing until an Admin turns them on, with recommended values
already filled in (5 wrong passwords → 15-minute lock; logout after 15 idle
minutes; keep entry records 365 days, unknown face data 30, gate photos 90;
ask-for-card range 0.10; 3 sightings within 10 minutes).

**Reset all to defaults.** A button at the top of the page puts every setting
back to its starting value (the value the system used before the Settings page
— `backend/.env` or the code's default — with the new features off). It asks
for confirmation first, then only fills in the values: nothing is saved until
the Admin reviews them and clicks "Save changes", which goes through the same
checks and writes one audit entry per setting that actually changed.

**How a change reaches the gates.**
1. The Admin edits a value; a "Save changes" bar appears (and the page warns
   before leaving with unsaved edits).
2. The server checks the value against its rules — type, minimum and maximum,
   and that "Frames that must agree" isn't more than "Out of the last". A bad
   value is refused with a plain-English reason, even if someone bypasses the
   page and calls the API directly.
3. **Risky settings** (Match strictness, Spoof check strictness, Spoof checking
   on/off) show their recommended range, warn when a value is outside it, need
   an explicit "Save anyway" confirmation (enforced by the server too), and have
   a "Reset to recommended" button.
4. The value is saved and an **audit log entry** is written: who, which
   setting, the old value, the new value, when. The page shows "Last changed by
   ___ on ___" under the setting.
5. The backend keeps settings in memory for at most 5 seconds, then re-reads
   them, so the very next camera frames use the new value.
6. Every `/api/identify` answer tells the gate monitor the current Match
   strictness and alert switches, so its status bar and alarms follow the change
   on the next frame — no restart.

**Verified** (rolled-back test, real enrolled photo compared against the
person's other poses): at Match strictness 0.45 the face (similarity 0.854) was
matched; after an Admin saved 0.90, the same face was not accepted, and the
value the gate monitor received and displayed changed from 0.45 to 0.90.

**What lives where.** Decisions are made by the backend, so almost every
setting is applied there. The gate monitor only applies the two alert switches
(and displays the strictness). The camera choice stays on each guard PC — the
camera number differs per machine, and only that PC can tell its camera works —
so it's set in the launcher and the gate monitor's camera picker, not here.

**Automatic deletion (Privacy & Data Retention).** `manage.py purge_old_data`
deletes, each by its own period: gate photos (the record stays), the face data
kept with Unknown/fake records plus the scan's short-lived working tables, whole
entry records, and — only if a period is set — old audit entries. It never
touches registered people, their face data or their registration photos (those
tables aren't even used by the command). `--dry-run` reports what it would
delete without deleting. It does nothing while "Automatic deletion" is off.
There's no job scheduler in this project, so the **launcher runs it** when the
backend comes up and once a day while it stays open. On a server that runs
without the launcher, use Windows Task Scheduler instead, e.g.:

    schtasks /Create /TN "SecureTap data clean-up" /SC DAILY /ST 02:00 /TR "cmd /c cd /d C:\path\to\EVSU-SecureTap-V2\backend && ..\.venv\Scripts\python.exe manage.py purge_old_data"

**Security & Accounts.** Failed login lockout is checked by the server at
sign-in (a locked account gets "Too many wrong passwords… try again in N
minutes", and the lock is audit-logged). Automatic logout runs in the dashboard:
any mouse, key, scroll or touch activity restarts the countdown, and the Login
page says why the user was signed out.

**Alerts.** "Alert on suspected fake face" only silences the gate monitor's
banner and sound — the event is still counted and logged. "Alert on repeated
unknown faces" makes the backend count how many times the same unrecognized
face has been logged at that gate within the set minutes; once it reaches the
set number, the gate monitor shows a "SAME UNKNOWN PERSON AGAIN" banner.

**Ask-for-card range (fake-face check).** When the fake-face score is just
under its limit — within this range — the face is matched as normal and, if it
matches someone, the gate asks for that person's card instead of flagging a
fake. If the card isn't tapped in time, it's logged as "Fake-face check unsure,
not confirmed by card tap in time."

### 8.1 Visual design system

The dashboard, the gate monitor and the launcher share one design system, taken
from the redesign mockups in `docs/Redesign UI/` (brief: `docs/design-brief.md`):

- **Colors** — a fixed palette of 17 tokens: inks and neutrals (`#121416`,
  `#4B5157`, `#8A9097`, line `#D9DCDF`, canvas `#EEF0F2`, white), EVSU maroon
  (`#7B1113`, deep `#4A0A0C`) with a brass accent (`#C89B3C`) used only for
  rules and active marks, and four status colors each with a tint — verified
  green, caution amber, danger red, and prompt blue. No gradients, no
  transparency. Status is never color alone: it always comes with an icon and a
  word (✓ Success, ⚠ Spoof suspected, ✋ Face covered, ...).
- **Type** — Archivo for display text, used at three widths (condensed for the
  small caps labels, semi-expanded for titles, expanded for the big numbers);
  Atkinson Hyperlegible Next for body text; IBM Plex Mono for data (IDs, times,
  confidence). The EVSU wordmark is set at the same size as "SecureTap"
  everywhere it appears.
- **Icons** — Phosphor (regular and bold).
- **Spacing** — an asymmetric scale of 4 / 6 / 10 / 16 / 26 / 42 / 68 / 110px,
  corners of 3 / 8 / 14px.

The dashboard loads the fonts from Google Fonts and the icons from the
`@phosphor-icons/web` package; the tokens live in `dashboard/tailwind.config.js`
and shared pieces (icon, page header, notice, avatar, segmented toggle) in
`dashboard/src/components/ui.jsx`. The two Windows apps can't use a web font, and
Tk on Windows can't select a width or weight out of a variable font, so
`entry-agent/assets/fonts/` bundles static cuts of each width/weight the design
uses (renamed "SecureTap …" as the SIL Open Font License asks of modified fonts;
licenses alongside) plus the Phosphor icon font. `entry-agent/ui.py` loads them
privately for its own process at startup — nothing is installed system-wide —
and falls back to stock Windows fonts if a file is missing.

---

## 9. entry-agent (gate device) features

**One window**, built with CustomTkinter. The entry-agent has no menu of its
own: choosing "Entry Agent" in the system launcher (§9.1) is already the
decision, so it opens straight onto the scanners, the feed and the live log
rather than a second screen asking the same question again. The gate monitor
owns the Tk root, so closing it ends the process.

- **Gate monitor** — a single window owning both credentials at once, so a guard
  watches one screen rather than alt-tabbing between two. It opens maximized.
  Under a maroon header (seal, EVSU SecureTap wordmark, "`<gate>` — live
  monitoring", a large clock and date, and a white ENTRY/EXIT pill) it splits
  into two columns, 7:5:
  - **Stats row** (top of the left column) — one large **Today** number, then
    **Entries / Unknown / Spoof / In frame**, each behind a thin divider. Driven
    by camera events and seeded from `/api/gate-summary`; card taps show in the
    live log but don't move these counters. Covered faces aren't counted
    (they aren't logged at all, §2.1 step 5).
  - **Live monitor** (left column, takes all its spare height) — CCTV-style
    continuous monitoring, not a one-person kiosk; several faces in frame are
    each identified independently (§2.1). Draws bounding boxes, each with a name
    tab, straight from the backend's `/api/identify` response (one source of
    truth — the entry-agent runs no local detector of its own). A strip across
    the top of the panel reads "■ LIVE · 1280×720 · 2 faces tracked"; the feed
    fills the rest of the panel edge to edge, trimming a little off its top and
    bottom if the panel's shape differs from the camera's (never more than 35% —
    past that the whole picture is shown with bars instead, so nobody at the edge
    of the camera's view can be cropped out). Green box = confirmed match (name ·
    confidence), amber box = confirmed unknown person, **red box = spoof
    suspected** (shown as soon as suspected), **blue "PLEASE UNCOVER YOUR FACE"
    box = face covered** (shown as soon as suspected), blue "TAP CARD TO
    CONFIRM" = ambiguous match or, for a confusable-pair-forced tiebreak
    specifically, "TAP CARD · LOOKALIKE CHECK" (§5.8) so it doesn't read as an
    ordinary uncertain match, gray "Checking…" = not yet confirmed. A newly
    confirmed unknown person or spoof also sounds an alarm and replaces the top
    strip with an amber UNKNOWN PERSON / red SPOOF SUSPECTED banner for 6
    seconds; a covered face gets no banner and no alarm (§2.1 step 11). **No
    camera connected** shows its own panel state — "No camera connected — card
    taps still work. Connecting a camera resumes automatically." — instead of a
    frozen or blank feed; the entry-agent starts up fine with no webcam at all (a
    gate can run on NFC taps and manual overrides alone), retries the connection
    on its own timer for as long as it runs, and picks a camera up automatically
    whether it's plugged in for the first time or was unplugged and reconnected
    mid-session — no restart needed either way.
  - **Card scanner** (bottom of the left column, a compact strip) — waits for an
    NFC tap (the reader emulates a USB keyboard; a hidden always-focused input
    field catches the typed card ID) — a tap is the only input, there is no
    typed-ID fallback (§2.2). Shows photo, name, role, ID, card ID and timestamp
    on success, or a specific failure reason (not registered / deactivated /
    read error) on rejection, then resets to "Tap a card" after 8 seconds.
  - **Live log** (the whole right column, header to status bar) — the newest
    event as a large card (a colored band with its word and time, then the
    photo, name, ID and match %), and every earlier one below it in a list, each
    row with photo, name, ID · time · confidence and its word. Both credentials
    land here: `ENTRY`/`EXIT` for a face match, `UNKNOWN`, `SPOOF`, and `CARD · ENTRY`/`CARD REJECTED`/`QUEUED` for an NFC tap — so the log is
    one chronological record of the gate regardless of how someone was
    identified. Non-routine rows get a tinted background and a colored left
    edge. An event whose encounter briefly included a covered face gets a
    "face briefly covered" note rather than losing that fact.
  - **Status bar** (bottom) — recognition threshold, **Camera OK / not
    connected** next to a **camera-picker dropdown**, the **Open student
    display** button (below), the officer name and app version on the left; Backend OK/not OK, offline-queue depth and sync state on
    the right. The camera chip
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
  - **Sized to the screen** — the layout is designed as a 1920×1080 screen and
    scaled to fit whatever screen it opens on (the usable area minus the
    taskbar and title bar), so it keeps the same proportions whatever Windows'
    display-scaling setting is — on a 1080p monitor at 125% it doesn't simply
    grow 25% and push panels off the edge. An un-maximized, narrower window
    shrinks toward a compact variant of the same layout, and the stats row wraps
    to a 2×2 block rather than being cut off.
  - **Student display** (`entry-agent/student_display.py`) — a second,
    student-facing screen, opened and closed from the status bar's **Open
    student display** button (it reads **Student display open · Close** while
    up). The gate monitor is the guard's screen; this one is for the people
    walking through, in large plain words: a maroon header ("EVSU · <gate>",
    "Welcome to campus", the clock), the same camera feed **mirrored** like a
    mirror so people find themselves in it, and a big message bar along the
    bottom saying what to do. Each face gets a label — **Welcome, <first
    name>** (green; first names only, since it's on public view), **Please see
    the guard** (unknown), **Show your real face** (confirmed spoof), **Please
    uncover your face**, **Tap your ID card** (tiebreak), or the backend's own
    hint ("Hold steady", "Face the camera") while still checking. The message
    bar speaks to whoever most needs to act (spoof, then unknown, tap, uncover,
    welcome), stays up a few seconds so it doesn't flicker between frames, and
    a card tap's result takes it over for 6 seconds — "Entry recorded", "This
    card is not registered", "This card is no longer active" (deactivated), or
    "Please tap your ID card again" for a misread or a server problem. With no camera it shows "Please tap your ID card".
    It runs no scanning of its own — the gate monitor hands it every
    `/api/identify` result and card tap, so the two screens never disagree. With
    a second monitor connected (a TV at the gate) it opens fullscreen there;
    otherwise it opens as a window to drag onto one — **F11** or a double-click
    toggles fullscreen, **Esc** leaves it. Card taps still work while it has
    focus (it passes the reader's typing to the gate monitor), and closing the
    gate monitor closes it too. Drawn from the redesign's 1920×1080 Student
    Display mockup and scaled to fit, letterboxed so its proportions never
    change.

**Offline resilience**: NFC tap lookups that fail due to a network error are
queued in a local SQLite database (`offline_queue.db`) and automatically retried
every 15 seconds once connectivity returns. A queued tap the backend refuses
outright (a malformed request, HTTP 4xx other than 401/403/408/429) is dropped
with a note in the console rather than retried forever, so it can't hold up the
taps queued behind it; a bad service token or a server error keeps the queue
intact for the next retry. Camera-scan frames are **not** queued
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

- **Dashboard** — opens the browser at `http://localhost:8000/`, where the
  backend itself serves the dashboard's ready-made build (`dashboard/dist`) — no
  separate dashboard server, and no Node.js needed to use the system. If the
  dashboard's code is newer than its build, the launcher runs `npm run build`
  first (only possible where Node.js is installed; otherwise it opens the
  existing build). Clicked before the backend is up, it waits and opens once it
  answers.
- **Entry Agent** — spawns `entry-agent/main.py` as its own process, which opens
  **straight onto the gate monitor**: the feed, the card scanner and the live log,
  with no intermediate menu. Kept a separate process rather than imported, since
  it owns a camera, worker threads and its own Tk main loop.

**First-time setup window (`setup_wizard.py`).** The first time the launcher
opens on a computer — no `device_profile.json` yet, or a missing `.env` — a
five-step setup window comes first; anything already done is just confirmed:

1. **Welcome** — creates `backend/.env` and `entry-agent/.env` from their
   templates with a fresh `DJANGO_SECRET_KEY` and one fresh gate key written
   into both files (`ENTRY_AGENT_SERVICE_TOKEN` / `SERVICE_TOKEN`), so they
   can't mismatch. An existing `.env` is never touched.
2. **Your data** — start empty, keep what's here, or bring everything over from
   an older SecureTap copy's folder (`manage.py import_securetap`, below).
3. **Admin account** — typed in here when there's no active Admin
   (`manage.py create_first_admin`, password passed on standard input, never the
   command line; the dashboard's own password rules apply). A fresh install
   therefore never has a default password anyone could guess.
4. **Camera and card reader** — the camera list the gate monitor's picker
   shows, the launcher's card-reader check, and whether the gate key matches
   (with a **Fix it** button if not). Informational only.
5. **Speed test** — picks a speed mode (§14) and lets the person override it.

**Bringing data over from an older copy (`import_securetap`).** For the
presentation laptop, which already has students enrolled in an older copy: the
command reads that copy's own `backend/.env` to find its database (MySQL or
SQLite) and copies — through the same code as `copy_mysql_to_sqlite`
(`configuration/datacopy.py`) — people and face data, entry records,
accounts, settings, the audit log; then the photos (`backend/media`) and the
trained covered-face model. The setup window then brings the gate's own
settings: the launcher's remembered gate/direction/guard name, and the
`entry-agent/.env` lines for gate name, camera and card reader — the gate
name matters because Security Officer accounts see only their gate's records,
matched by its exact name. Safety rules, each tested:
- **The old copy is never changed.** A SQLite source is read from a temporary
  copy, because merely opening an SQLite file can make it finish a half-written
  change.
- **Version check first.** The old copy's applied migrations are compared with
  this code's. Same → copied as is. Older SQLite → its temporary copy is
  updated first (in a separate `manage.py migrate`, so data-migration steps run
  against the right database). Older MySQL, or a newer copy → refused, with what
  to do.
- **Nothing half-done.** This copy must be empty (or `--replace`, which keeps
  the current database as `db.sqlite3.bak`); a failed or incomplete copy is
  thrown away and the previous database put back. Every table's row count is
  compared afterwards, and a few stored faces are re-computed from their photos
  (similarity must be ≥ 0.98, else `recompute_embeddings` runs).

**Where the data lives (`SECURETAP_DATA_DIR`).** An installed copy can't
write into its program folder, and reinstalling it must never touch the
students' data, so everything the system writes goes to one data folder:
`backend.env`, `entry-agent.env`, `db.sqlite3`, `media/`, the covered-face
model and its training photos, the card-tap queue, the last-session summary,
`device_profile.json` and `launcher_settings.json`. `device_setup.py` decides
the folder — `SECURETAP_DATA_DIR` if set, else `%LOCALAPPDATA%\EVSU SecureTap`
when the installer's `securetap-installed.txt` marker is present — and sets
`SECURETAP_DATA_DIR` for everything the launcher starts; the backend
(`settings.DATA_DIR`) and the gate monitor (`config.data_file`) only read it.
Running from the code folder (no marker, nothing set), every file stays where
it always was. Tested by running a code-only copy with the marker through the
whole setup, the backend and the gate monitor: nothing new appeared in its
program folder.

**Speed mode row.** Under the Entry Agent settings, a collapsed **Speed mode**
row shows this computer's mode and the last speed test; it switches between
Fast / Standard / Light, re-runs the test, and offers **Run setup again**, which
stops everything, closes the launcher and reopens the setup window (to bring
data over later, for instance) — the launcher comes back afterwards.

Before opening the gate monitor, the launcher now also handles a few things a
guard would otherwise only discover once already standing at the gate:

- **Gate, direction, and guard name** are editable in a collapsed-by-default
  "Entry Agent settings" panel and remembered locally between launches
  (`launcher_settings.json`, a per-machine file, not something checked into the
  repo) — see the entry-agent's own **Configuration** paragraph above for exactly
  how these three values reach that process without editing `.env`.
- **Pre-flight checks** run right before the Entry Agent button actually spawns
  the process: whether the backend currently responds, and a best-effort check
  for the NFC card reader via Windows' own Plug-and-Play device list, matched
  against a list of known reader USB vendor:product IDs. The default list is
  `072F:2200` (a genuine ACS ACR122U) and `FFFF:0035` (the unbranded
  keyboard-emulation reader this gate actually has — it's sold as an "ACR122U"
  but doesn't report ACS's vendor ID, which is why an ACS-only check warned
  about a missing reader while it was plugged in). A different reader is added
  with `NFC_READER_USB_IDS` in `entry-agent/.env` (comma-separated `VID:PID`
  pairs, found in Device Manager → the reader → Properties → Details →
  Hardware Ids). Matching by ID is the only practical check — Windows sees any
  HID-emulation NFC reader as a generic keyboard, with no NFC-specific identity
  to query in general (see §10's note on why the gate monitor's own live
  "ready" indicator can't do this). Neither check
  can block opening the gate monitor on its own — a negative result shows a
  "Before you open the gate monitor" dialog listing each warning, with **Open
  gate monitor** (the default — it opens anyway) and **Cancel**, since a live
  demo or a real shift can't afford to be blocked by a pre-flight check that's
  wrong, but the guard may still want to fix the problem first.
- **Startup notices.** If the offline queue (see "Offline resilience" above) has
  any NFC taps still waiting to sync, or a record exists of the previous gate
  monitor session (written to `last_session.json` when that window closes, with
  its final entries/exits/unknown/spoof/occlusion counts, gate, direction, and
  when it ended), both show on the launcher's main screen before anything is
  opened at all — the last session as a line, the unsynced taps as an amber
  notice.
- **Auto-launch.** An optional setting in the same panel opens the gate monitor
  automatically a moment after the launcher itself starts, for a kiosk-style
  deployment where nobody should need to click anything.

**Layout.** Top to bottom: a maroon header (seal, EVSU SecureTap wordmark,
"Choose what to open"); a **services bar** showing Backend, Dashboard and Entry
Agent each as an icon + word (Ready / Running / Starting… / Not running /
Failed), with a note under it when there's something to add ("Using a backend
that was already running."); the two **choice cards**, Dashboard and Entry
Agent; the startup notices; and one card holding the collapsed **Entry Agent
settings** (showing the current gate and direction) and **Show log** (showing
how many lines it holds). A footer carries the version and a red-outlined
**Quit**.

The window is responsive rather than fixed-size: its content is capped at a
comfortable reading width and centered instead of stretching edge-to-edge on a
large monitor, the two choice cards sit side-by-side when each has room for at
least 360px and stack below that, and the header grows when the window is
maximized. In the services bar, a service's state sits beside its name when
there's room and drops under it when there isn't, so the default-size window
never squeezes one over the other. The current app version (the same value the
gate monitor's status bar shows) appears in the launcher's footer too, so both
windows always agree on it.

**Loading animation.** Both choices take several seconds before anything visible
happens — the entry-agent measured ~3.6–4.2s (importing `cv2`, opening the webcam
through DirectShow, building the window), and a dashboard rebuild (only after its
code changed) takes a similar time. While that runs, the card grows a status strip — "Starting the
camera and opening the gate monitor…" over a moving progress bar — so the wait
reads as work rather than as a button that missed the click. The strip then
settles to a green "Gate monitor is open" / "Open at localhost:8000 — click to
open it again", a red "Failed to start — open the log below to see why", or, after 45
seconds with no signal, an amber "Still not up after 45s".

It ends on a real signal, not a timer. `main.py` prints
`SECURETAP_ENTRY_AGENT_READY` on the line immediately before handing off to its
Tk loop; the launcher watches its output for that marker and stops the animation
when the window is genuinely up. The dashboard's strip ends when its rebuild
finishes and the backend answers. Three ways out, so a spinner can never outlive what it's waiting for:
the ready marker, the process dying (the status poll notices and shows "Failed to
start"), or a 45-second timeout.

Design points worth knowing:

- **Adopts an already-running backend.** Before starting anything it probes
  `/api/health`. A developer who already has `runserver`
  open in a terminal would otherwise get a second one that dies on "port already
  in use" — while the health probe kept answering from the *first* instance, so
  the launcher would show a green light beside a dead child. Adopted services are
  never killed on quit; only what the launcher started is.
- **Process trees, not processes.** Children are stopped with `taskkill /T`,
  because `npm` spawns `node` as a child and terminating `npm` alone would leave
  a dashboard build running on.
- **Output is captured, not discarded.** Each child's stdout+stderr is drained on
  its own thread into a rolling buffer behind a "Show log" panel. Draining isn't
  optional: an unread pipe fills at ~64KB and the child then blocks forever on its
  next write. The log is also where a backend that failed to start (a bad
  `.env`, a port already taken) explains itself, instead of the button just appearing to do nothing.
- **Threading follows the same rule as the gate monitor** — output readers only
  touch plain data; every widget update happens on the Tk main thread.
- The design system (color tokens, fonts, icons — §8.1) is imported from
  `entry-agent/ui.py` rather than duplicated, so the launcher and the gate monitor
  can't drift into looking like two different products.

There's no database server for the launcher to start: the default SQLite
database is a file the backend opens itself. (With the optional
`DB_ENGINE=mysql`, MySQL runs as its own Windows service, outside the
launcher's control.)

**Why SQLite (and how MySQL compares).** For one laptop running the whole
system — the capstone defense — a separate database server is only something
extra to install, configure and keep running. SQLite stores everything in one
file with nothing to install, and with WAL mode it handled a 90-second
busy-gate test (two gates scanning ~5 frames a second each, card taps, the
dashboard's live page and settings saves at once — 857 requests) with zero
errors and zero "database is locked". It also fixed a hidden MySQL problem:
filtering the Logs page by date returned nothing on a MySQL server without its
time-zone tables loaded, while SQLite returns the right records. MySQL remains
an option for a real deployment with several gates and a dedicated server.

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
- **Settings page (§8.2) limits** — there's still no gate list: gate names
  remain free text typed on each guard PC and on each Security Officer account,
  so "default direction per gate" isn't possible either (direction is set per
  guard PC). The number of enrollment photos is fixed at 5 (one per guided
  pose). Automatic logout is enforced in the dashboard, not by the server — a
  stolen token still lasts until it expires (60 minutes, renewable for up to 12
  hours). A second backend process (if one were ever run) can take up to 5
  seconds to see a change. "Allow single-photo registration" covers the Add
  Person form, not bulk import.
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
  and overall face-likeness. **A trained classifier is the upgrade path, and is
  now usable live behind `OCCLUSION_DETECTION_MODE=classifier` (§2.1 step 5):**
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
  photos and saves the trained model to `backend/users/occlusion_classifier.joblib`,
  the file classifier mode loads. On the current training set (124 clean, 186
  covered photos) the model flags 19 of the 124 clean photos as covered and
  catches 153 of the 186 covered ones — measured on its own training data, so
  the real-world rate is likely worse, especially for faces far from the camera.
  The `_already_recognizable` skip (§2.1 step 5) removes those false alarms for
  anyone enrolled; they can still reach an **unenrolled** person with an
  uncovered face, who may briefly be shown "please uncover your face". Since
  covered faces aren't logged, the Unknown vote is only held back while covered
  reads are the majority (§2.1 step 5), so a stranger misread only now and then
  is still logged as Unknown, with the alarm. The fix for that is more and
  better training data — more clean photos taken at the real gate camera and
  distance, until clean and covered are roughly balanced — then retraining.
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
  check *before* opening the gate monitor: a best-effort search for a known
  reader in Windows' own connected-device list, by USB vendor/product ID
  (§9.1, configurable with `NFC_READER_USB_IDS`) — real for the readers on
  that list, but not a general "is any NFC reader plugged in" answer, and it only
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

### 13.4 Guard sign-in at the gate monitor

Off by default (Settings → Security & Accounts → **Guards sign in at the gate
monitor**). The gate monitor itself proves *which device* it is with the gate
key; this proves *which guard* is on duty, so "who was watching the gate?" has
an answer.

- **Signing in.** The status bar shows an amber **No guard signed in · Sign
  in**. The guard either types their own dashboard username and password, or
  **taps their own staff ID card** — `AdminProfile.staff_card_id`, set once on
  the Accounts page by tapping the card into the field. A staff card is never
  looked up as a student: `/api/verify` checks staff cards first and never logs
  one as a gate entry. A card number can't be both a staff card and a student's
  (checked both ways). The sign-in window tells a card tap from typing by
  speed (`ui._KeyBurst`): the reader types its whole number within a few
  hundredths of a second, so a burst like that ending in Enter is a card,
  whichever box had focus.
- **Who may sign in where.** A Security Officer only at their assigned gate
  (the same exact-name match as §13.3); Admin and SASO at any gate. Wrong
  passwords count toward the failed login lockout, shared with the dashboard
  sign-in (`accounts/lockout.py`). Every request also needs the gate key, so
  the endpoints (`/api/gate/sign-in`, `/api/gate/sign-out`) can't be used from
  anywhere but a gate monitor.
- **Shifts.** A sign-in starts a `logs.GateShift`. It ends when the guard
  clicks **Sign out**, another guard signs in at the same gate (shift change),
  the gate monitor closes or reopens (a forgotten sign-out can't carry over),
  or after the shift time limit (Settings, 12 h). A staff card tap that was
  saved while the gate was offline never signs anyone in when it's sent later.
  Every sign-in and sign-out is in the Audit Log, with how long the shift was.
- **The records.** Each `EntryLog` row stores the guard on duty (`on_duty`),
  or `unattended` when sign-in is on but nobody was signed in — filled in by
  `EntryLog.save()` itself, so no code path that logs an entry can skip it. The
  gate keeps scanning either way: an unattended gate still logs everyone; it's
  just marked. The Logs page shows "On duty: name" or an amber "Unattended"
  under the gate, and the CSV export has an "On duty" column.
- **Updates.** The launcher applies database updates (`manage.py migrate`)
  each time before it starts the backend, so installing a newer version over
  an older one adds the new fields by itself.

## 14. Running on a low-power laptop

The system is presented on an **Intel N100** laptop (4 cores, 8 GB RAM, Intel
UHD graphics). On a chip like that, the face AI, the gate monitor's video, the
backend, the dashboard in the browser and Windows all compete for the same 4 cores, so the
system was tuned to waste as little CPU as possible — and since every computer
is different, it measures each one and adjusts itself (speed modes, below). **None of this changes
who gets recognized**: re-computing stored enrollment photos with the tuned
code gives fingerprints identical to the stored ones (similarity 1.000), and
match strictness, voting and every safety check are unchanged.

**What changed (always on):**
- **The face AI is limited to part of the CPU** (`AI_THREADS`, automatic by
  default — 2 of the N100's 4 cores). Left alone it starts a worker per
  processor on every camera frame and the rest of the system stutters.
  Measured on the development PC limited to 4 cores: **277 ms → 18 ms per
  frame**.
- **One copy of the face AI instead of two.** Enrollment and the gate scan
  share the same loaded models and only ask the face finder to search at a
  different size (640 for posed enrollment photos, `GATE_SCAN_DET_SIZE` at the
  gate). Model memory ~120 MB → ~90 MB, and faster start-up.
- **Cheaper gate-monitor video.** A faster resize, the rounded-corner mask
  built once instead of every frame, and ~15 frames a second instead of ~24
  (the Student Display ~10). Measured: **381 → 129 ms of CPU per second** of
  video.
- **Smaller frames sent to the backend** — JPEG quality 85 instead of 95.
- **The backend runs without the auto-restart helper** (`runserver
  --noreload`), which otherwise re-checks every code file each second. After
  editing backend code, restart it.
- **The dashboard is a ready-made build served by the backend** (§9.1): plain
  files, no live recompiling and no Node.js process, and SQLite needs no
  database server — two fewer programs running on the laptop.

**Speed modes, picked automatically per computer (`device_setup.py`).** The
first-run setup (§9.1) runs a speed test — `manage.py benchmark_scan --sample
--json`, which times 20 of the gate's face checks (find faces, fingerprint,
fake-face check) on a camera-shaped frame cut from a public test photo that
ships with InsightFace, so it works before anyone is enrolled and measures the
same picture on every computer — and picks one of three modes:

| | Fast | Standard | Light |
|---|---|---|---|
| Face-search size (`GATE_SCAN_DET_SIZE`) | `.env` (480) | `.env` (480) | 384 — faces within ~2-3 m |
| Frame size sent (`UPLOAD_MAX_DIMENSION`) | `.env` (960) | `.env` (960) | 640 |
| Gate monitor video (`VIDEO_FPS`) | 20 fps | 15 fps | 12 fps |
| Pause between checks (`SCAN_PAUSE_SECONDS`) | 0.1 s | 0.2 s | 0.35 s |
| Student Display (`STUDENT_DISPLAY_FPS`) | 15 fps | 10 fps | 6 fps |

Rule: **Light** when one face check takes ≥ 90 ms, *or* the computer has ≤ 4
processor cores, *or* < 6 GB of memory — the video, face checks, backend and
browser all share the processor, so a 4-core chip like the N100 gets Light even
when one check alone is quick; **Fast** when a check takes < 35 ms on 8+ cores
and 8+ GB; **Standard** otherwise. The reason is shown in plain words ("Each
face check took 55 ms, but this computer has only 4 processor cores…"), and the
person can pick another mode on the spot or later in the launcher's **Speed
mode** row, which also re-runs the test. Measured on the development PC:
18–20 ms per check, 16 cores → Fast.

The choice is stored in `device_profile.json` (per computer, git-ignored) and
passed to the backend and the gate monitor as **environment overrides** when the
launcher starts them — the same mechanism as the gate name and direction, so the
`.env` files are never rewritten. Standard passes nothing, leaving every `.env`
value as it is. Switching modes restarts the backend by itself when its part
changes (the face-search size is read once, at start); an open gate monitor
picks the new mode up when reopened. The face model, match strictness, voting
and every safety check are the same in all three modes.

**Measuring by hand.** `python manage.py benchmark_scan` (in `backend/`) times
the scan on an enrolled photo (or the test photo with `--sample`) and reports
time per frame and memory; `--det-size 384` compares the Light size, `--cores
4` imitates a 4-core laptop.

**Presentation-day checklist:**
1. Laptop plugged in; Windows power mode set to **Best performance**.
2. Close other apps and browser tabs; pause Windows Update.
3. The laptop runs the **installed** copy (§15) — after any code change, build
   a fresh `EVSU-SecureTap-Setup.exe` and install it over the old one (the data
   stays).
4. Start the system from its desktop icon a few minutes before the panel
   arrives, open the dashboard and the gate monitor once, and let a face be
   recognized — the first scan loads the AI models, so get that out of the way.
5. Check the launcher's **Speed mode** row says Light (the N100's expected
   pick); after changing anything about the laptop, **Run the speed test again**.

## 15. The installer (`installer/`)

So a new computer needs nothing but one file, `installer/build.py` (run with
the project's venv on the development computer) produces
`installer/output/EVSU-SecureTap-Setup.exe` (~170 MB, ~716 MB installed):

- **Its own Python** — Python 3.14 from python-build-standalone (the portable,
  relocatable build `uv` uses, which includes tkinter for the windows), with
  every library installed at **exactly the versions the venv runs**: the build
  pins them from `pip freeze`, because `requirements.txt`'s lower bounds alone
  pulled in InsightFace 2.1 and ONNX Runtime 1.30 — untested versions of the
  face AI. MySQL support is included so an older MySQL copy can be imported.
- **Microsoft's C++ runtime** files (`msvcp140.dll`, …) next to Python — ONNX
  Runtime and OpenCV need them and a fresh Windows may lack them; Microsoft
  allows shipping them this way.
- **Only git-tracked program files** (`git ls-files`), so no database, `.env`,
  photos, training data or trained model can be packaged.
- **The dashboard**, freshly built, so the target never needs Node.js.
- **Two face models** (`det_500m.onnx`, `w600k_mbf.onnx`, 15 MB) in
  `face-models/` — the only two `insightface_utils` loads, instead of the whole
  158 MB pack — which `settings.INSIGHTFACE_ROOT` picks up, so the first
  enrollment or scan needs no internet.
- **`securetap-installed.txt`**, the marker that sends all data to
  `%LOCALAPPDATA%\EVSU SecureTap` (§9.1).

Before packaging it checks the bundle imports every library and passes
`manage.py check`. `installer/SecureTap.iss` (Inno Setup 6) installs per user —
no admin prompt — into `%LOCALAPPDATA%\Programs\EVSU SecureTap`, with Start
menu and desktop shortcuts that run the launcher through `pythonw.exe` (no
console window; the launcher gives its child programs `python.exe` so their
output still reaches its log). A newer version replaces the program files
only; uninstalling removes them and asks (default: no) before deleting the
data folder. The file is unsigned, so Windows SmartScreen asks once ("More
info → Run anyway").

**Clean-computer test.** `installer/sandbox_test.py` opens Windows Sandbox — a
throwaway Windows — with networking disabled, installs the Setup.exe silently
and runs `installer/sandbox/check_install.py`: install location and data
folder, the settings files with a matching gate key, the database, the first
Admin, the backend starting, the dashboard served, an Admin login, the
Settings API, the face-AI speed test (offline, from the bundled models), the
gate monitor's and launcher's code loading, nothing written into the program
folder, and the desktop shortcut. All 14 passed; the speed test there picked
Light (the Sandbox has 4 GB of memory) at 21.6 ms per face check. With
`--try` it instead opens a Sandbox to click through by hand: the installer on
its desktop, the host's webcam shared (`VideoInput`), 8 GB of memory like the
presentation laptop, no internet. The NFC reader can't be tested there — the
Sandbox doesn't pass USB devices through.

**Deploying to the presentation laptop.** That laptop already holds an older
copy (MySQL) with the students enrolled, so the installed app takes its data
rather than starting over: with the old copy closed (both use port 8000) and
its MySQL service running, install the Setup.exe and choose **Bring the data
from an older copy** in the setup window (§9.1 — `import_securetap` reads the
old copy's own `backend/.env` for the MySQL login, never changes it, and checks
row counts and face fingerprints afterwards). The Admin step is skipped
because the accounts come along; the speed test should pick Light. The old copy
stays as the backup; once the new one is verified, MySQL is no longer needed
(it can be set to Manual to free memory). A later code change ships as a new
Setup.exe installed over this one — the data folder is untouched. The step-by-
step checklist is in README.md, "Putting it on the demo laptop".
