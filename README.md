# EVSU SecureTap

Campus entry monitoring system for Eastern Visayas State University, with a real-time admin dashboard.

**How the gate check works:** the webcam continuously scans everyone passing the gate (like a CCTV feed) and checks each face against every enrolled student/staff photo (a 1:N identification search, via InsightFace/ArcFace) - no card tap required. A match isn't trusted from a single frame: the same person has to be the top match across several recent frames before it's logged as a real entry, and a blurry or partially-out-of-frame frame is skipped rather than risked. The NFC card reader is a secondary, optional channel - normally it's just a lookup for the guard to visually cross-check, but if the camera sees a borderline/ambiguous match it will ask for a confirming card tap to break the tie. Recognized people are logged automatically; an unrecognized face is flagged as not enrolled.

## Project layout

```
evsu-securetap/
├── backend/       # Django + DRF API (accounts, users, logs, reports apps) + MySQL
├── entry-agent/   # Windows app: webcam capture + NFC card read -> calls the API
├── dashboard/     # React + Tailwind admin dashboard
├── docs/          # SQL snippets, supplementary docs
└── README.md
```

## Prerequisites

- Windows 10/11
- MySQL Server 8.0+ running locally (as the `MySQL80` service, or similar)
- Node.js 20 LTS or newer (developed/tested with Node 25) + npm
- Python 3.11–3.14, plain CPython from [python.org](https://www.python.org/downloads/) - **no conda/Miniforge needed**. The old dlib-based face recognition required conda for a prebuilt binary; the current InsightFace/ONNX Runtime stack has normal prebuilt PyPI wheels, so plain `pip` works fine.
- Git
- A webcam
- Optional: a USB NFC/RFID reader in HID-keyboard-emulation mode (types the card ID + Enter - see the entry-agent section below)

## 1. Get the code

```powershell
git clone https://github.com/ZJake15/EVSU-SecureTap-V2.git
cd EVSU-SecureTap-V2
```

## 2. Database setup

Open `docs/create_database.sql` and replace `CHANGE_ME_TO_A_STRONG_PASSWORD` with a password you choose (see "Getting your secrets" below for how to generate one), then run it against your MySQL server as an admin user (root):

```powershell
mysql -u root -p < docs/create_database.sql
```

(No `mysql` CLI on PATH? Paste the file's contents into MySQL Workbench or HeidiSQL instead - just make sure you've edited the password first.)

This creates a dedicated `securetap` database and a least-privilege `securetap_app` user, so the Django backend never needs your root credentials. **Remember the password you chose** - it goes into `backend/.env` as `DB_PASSWORD` in the next step.

## 3. Backend setup (Django)

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
# If PowerShell blocks this with an execution-policy error, run:
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
# then retry the activate command above (this only affects the current
# terminal session, not the whole machine).

# --only-binary=:all: skips straight to prebuilt wheels. On a very new Python
# version, a plain `pip install` can look stuck for several minutes - pip's
# resolver is scanning years of old scipy/etc. releases for compatibility,
# not actually hanging - this flag avoids that entirely.
pip install -r requirements.txt --only-binary=:all:

copy .env.example .env
# edit .env now - see "Getting your secrets" below for DB_PASSWORD,
# DJANGO_SECRET_KEY, and ENTRY_AGENT_SERVICE_TOKEN specifically.

python manage.py migrate
python manage.py seed_dummy_data
python manage.py runserver
```

Backend runs at `http://localhost:8000`. Django admin at `http://localhost:8000/admin` (log in with a seeded admin account below, or `python manage.py createsuperuser`).

**First run only:** the first request that touches face recognition (enrolling someone, or the entry-agent's first scan) downloads the InsightFace `buffalo_l` model (~280MB) automatically, cached afterward under `%USERPROFILE%\.insightface\models\`. Needs internet access the first time; instant after that.

Seeded demo dashboard logins (from `seed_dummy_data`) - **change these passwords before any real deployment**:

| Username | Password | Role |
|---|---|---|
| `admin_demo` | `ChangeMe123!` | admin |
| `security_demo` | `ChangeMe123!` | security |
| `it_demo` | `ChangeMe123!` | it |

The seeded dummy *people* (students/staff) have **random, fake face embeddings** - they're only useful for exercising the logs/reports/listing UI, not real face matching. Register at least one real person with real photos via the dashboard's Users page (guided 5-photo capture, or the single-photo fallback) so the continuous scan has someone real to recognize.

## 4. Entry-agent setup (Windows, plain Python)

The entry-agent doesn't run face recognition itself - it captures frames and posts them to the backend, which does the actual matching, so it needs no special ML dependencies.

```powershell
cd entry-agent
python -m venv .venv
.venv\Scripts\Activate.ps1
# If PowerShell blocks this with an execution-policy error, run:
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
# then retry the activate command above (this only affects the current
# terminal session, not the whole machine).
pip install -r requirements.txt

copy .env.example .env
# SERVICE_TOKEN must match ENTRY_AGENT_SERVICE_TOKEN in backend/.env exactly.
python main.py
```

Two things happen once it's running:
- **Continuous scan** (primary): every ~0.2s it grabs a webcam frame and posts it to `/api/identify`. A match needs to agree across several recent frames before it's confirmed (not a single frame); a blurry or partially-out-of-frame face is skipped and shown as "Checking..." rather than risked as a false "Unknown". A recognized person shows a green box + name + confidence; a confirmed unrecognized face shows an amber "Unknown" box; a borderline/ambiguous match shows a blue "Tap card to confirm" prompt.
- **Card tap** (secondary): posts to `/api/verify` - normally just a lookup that shows the guard the tapped person's on-file photo for a visual cross-check, but if the camera currently has a pending tiebreak for this gate, the tap resolves that instead. Either way, a successful tap is logged as a real gate entry (deduped if the same card is tapped again within the cooldown window) and appears in Live Monitoring/Logs like a face-scan entry does.

**About the NFC reader:** cheap "13.56MHz IC / 125KHz ID" combo readers (and many similar low-cost USB RFID modules) are **not** PC/SC smart-card devices - they're USB HID-keyboard-emulation devices. Tapping a card literally "types" the card's ID followed by Enter into whatever window currently has keyboard focus, exactly like a very fast typist. Because of this:

- The entry-agent's card-scanner window keeps a hidden, always-focused input to catch that typing - **keep its window focused/on top** rather than clicking into some other application while it's running.
- There's no separate reader-detection step and no Windows service dependency for this - if it types into Notepad, it'll type into the entry-agent.
- **Tip:** to find out what ID a given card produces (e.g. when registering a new person), click into the "NFC ID" field on the dashboard's Add User form and tap the card there - it types the ID directly into that field.
- If you have a genuine PC/SC reader instead (e.g. an actual ACR122U), this approach won't see it; that would need reintroducing `pyscard` and reading via APDU commands instead.

Requirements:
- A webcam (used continuously - keep it unobstructed and aimed at the gate)
- The USB NFC/RFID reader plugged in, optional (no driver install needed - Windows sees it as a generic keyboard)
- Optional/experimental: `CAMERA_EXPOSURE` in `entry-agent/.env` can force a shorter exposure to reduce motion blur, but manual-exposure support varies a lot by webcam/driver - leave it unset unless you've confirmed it works on your specific camera (see the comment in `entry-agent/camera.py`).

## 5. Dashboard setup (React + Vite + Tailwind)

```powershell
cd dashboard
npm install
copy .env.example .env
npm run dev
```

Dashboard runs at `http://localhost:5173` (Vite picks the next free port, e.g. `5174`, if that one's busy - check the terminal output).

## Getting your secrets

Three values need to be filled in before anything will run - none of them are things you "look up" anywhere, they're generated or chosen locally:

### `DB_PASSWORD` (`backend/.env`)
Not a secret handed to you - **you choose it**. Pick a strong password, put it in `docs/create_database.sql`'s `CREATE USER ... IDENTIFIED BY '...'` line (replacing the placeholder), run that script, then put the exact same password in `backend/.env`'s `DB_PASSWORD`. To generate a random one instead of making one up:
```powershell
python -c "import secrets; print(secrets.token_urlsafe(24))"
```

### `DJANGO_SECRET_KEY` (`backend/.env`)
Django's own official generator - run this from inside the backend's activated venv (needs Django installed):
```powershell
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```
Paste the output into `DJANGO_SECRET_KEY`. Keep it private (it signs sessions/tokens) and use a different one per environment - never reuse a key between your dev machine and a real deployment.

### `ENTRY_AGENT_SERVICE_TOKEN` (`backend/.env`) / `SERVICE_TOKEN` (`entry-agent/.env`)
Just a shared secret the backend and entry-agent both know and compare directly (not JWT-based, since the entry-agent is a trusted device, not a logged-in user) - any long random string works:
```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```
Paste the **same value** into both `backend/.env`'s `ENTRY_AGENT_SERVICE_TOKEN` and `entry-agent/.env`'s `SERVICE_TOKEN` - they must match exactly, or the entry-agent gets rejected with a 401/403 and its status strip shows the server as unreachable.

> **Security note on this repo's history:** `docs/create_database.sql` previously had a real password committed in plaintext (matching what was, at the time, the actual local dev database password). It's been replaced with a placeholder, but that old value is still recoverable from git history on whichever repos it was pushed to. If you ever used that exact password for a real MySQL user, treat it as compromised and change it - don't just rely on the file being fixed going forward.

## Running everything together

1. MySQL running (Windows service).
2. `cd backend`, activate its venv, `python manage.py runserver`.
3. `cd dashboard`, `npm run dev`.
4. `cd entry-agent`, activate its venv, `python main.py`.
5. Log into the dashboard and register a real person via the Users page - guided capture walks through 5 near-frontal shots (front, slight left/right turn, neutral, smile), or use the single-photo fallback if you're staging one for bulk import instead. The entry-agent should recognize them automatically within a few seconds of facing the webcam, after enough frames agree - no tap needed. Optionally tap their NFC card too and confirm their photo pops up on the entry-agent window. Both should appear on the dashboard's Live Monitoring page and in Logs.

## Data privacy notice

This system processes **biometric data** (face embeddings, reference photos) on **everyone who passes the gate**, not just people who choose to tap a card - the continuous camera scan identifies every face it sees, including visitors and anyone else in view. This is a meaningfully bigger privacy footprint than a purely tap-triggered check, and falls under the **Philippine Data Privacy Act of 2012 (RA 10173)**. Before deploying this at an actual gate:

- Collect explicit written consent for biometric data collection and processing from every student/staff member being enrolled.
- Post clear, visible notice at the gate that facial recognition is in continuous operation, covering anyone who passes through - not just enrolled students/staff.
- Explain how the data will be used, stored, retained, and for how long.
- Provide a documented way to request correction or deletion.
- Confirm this broader always-on scanning scope (versus a consent-gated tap-triggered check) is something the institution's data protection officer/process has actually signed off on.

The system stores face **embeddings** (512-dimensional ArcFace vectors), not raw images, in the database specifically to reduce biometric exposure. Reference/enrollment photos are kept separately (`backend/media/`) only for dashboard/guard display and re-computing embeddings if the model ever changes, and should be access-controlled in any real deployment.

## Architecture notes

- **Continuous 1:N face identification** (`POST /api/identify`) is the primary gate check - it compares a sampled frame's embedding against every enrolled active person's stored embeddings (a vectorized cosine-similarity scan, fast enough for hundreds of enrolled people/embeddings, not built to scale to a huge student body without a proper vector index like FAISS). A single frame is never trusted alone: the same person has to be the top match across several recent scans (a rolling-window majority vote) before a real log row is written, and the same grace period applies to "Unknown" so a single blurry frame can't falsely flag someone. A borderline or genuinely ambiguous match (two similar-looking candidates) triggers an NFC tiebreak instead of guessing.
- **`POST /api/verify`** handles a card tap or the manual ID-entry fallback - normally a lookup that returns the matched person's profile for the guard to visually cross-check, but resolves an active NFC tiebreak if one is pending for that gate. A successful tap logs a real gate entry (deduped against a recent tap of the same person).
- **Real-time updates** use short-interval polling (`GET /api/logs/live`) rather than WebSockets/Django Channels - simpler to run reliably without adding Redis + an ASGI server.
- **`admin_accounts`** is implemented as Django's built-in `User` model plus an `AdminProfile(role)` model (see `backend/accounts/`), with `bcrypt` configured as the primary password hasher - not a hand-rolled auth table.
- The entry-agent authenticates to `/api/verify` and `/api/identify` with a shared-secret `X-Service-Token` header, not a JWT - it's a trusted device, not a logged-in dashboard user.
- **Bulk import** (`POST /api/users/bulk-import`) matches photos already placed on the server at `backend/seed_data/photos/<student_or_employee_id>.jpg` by filename, and flags the resulting single-photo enrollment as lower-confidence in the data.
- The entry-agent queues NFC tap lookups (not scan frames) in a local SQLite file (`entry-agent/offline_queue.db`) when the backend is unreachable, and a background thread syncs them automatically once connectivity returns.
- The NFC reader is read as HID-keyboard-emulation input (a focused, off-screen Tkinter `Entry` widget) rather than through `pyscard`/PC-SC - see the note in the entry-agent section above. The continuous scan and each tap lookup run on their own worker threads so camera/network I/O never freezes the feedback window.
