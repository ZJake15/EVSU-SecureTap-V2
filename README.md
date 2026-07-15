# EVSU SecureTap

Campus entry monitoring system for Eastern Visayas State University, with a real-time admin dashboard.

**How the gate check works:** the webcam continuously scans everyone passing the gate (like a CCTV feed) and checks each face against every enrolled student/staff photo (a 1:N identification search) - no card tap required. Recognized people are logged automatically; an unrecognized face is flagged as not enrolled. The NFC card reader is optional/secondary: tapping a card doesn't re-check anything, it just looks the card up and pops the person's on-file photo onto the guard's screen as a manual visual cross-check, with an error if the card isn't linked to an enrolled, active person.

## Project layout

```
evsu-securetap/
├── backend/       # Django + DRF API (accounts, users, logs, reports apps) + MySQL
├── entry-agent/   # Windows script: NFC card read + webcam capture -> calls the API
├── dashboard/     # React + Tailwind admin dashboard
├── docs/          # SQL snippets, supplementary docs
└── README.md
```

## Prerequisites

- Windows 10/11
- MySQL Server running locally (as the `MySQL80` service, or similar)
- Node.js + npm (for the dashboard)
- Python 3.13 (system install - used directly by the entry-agent)
- Miniforge/conda (used only for the backend - see step 2)
- A USB NFC/RFID reader in HID-keyboard-emulation mode (types the card ID + Enter - see step 3) + a webcam

## 1. Database setup

Run `docs/create_database.sql` against your MySQL server as an admin user (root):

```powershell
mysql -u root -p < docs/create_database.sql
```

(No `mysql` CLI on PATH? Paste the file's contents into MySQL Workbench or HeidiSQL instead.)

This creates a dedicated `securetap` database and a least-privilege `securetap_app` user, so the Django backend never needs your root credentials.

## 2. Backend setup (Django)

`face_recognition` depends on `dlib`, which has **no prebuilt Windows wheel on PyPI** - only a source tarball that needs CMake + a C++ compiler to build. We sidestep that entirely by installing dlib from **conda-forge**, which publishes a working prebuilt binary.

```powershell
# One-time: install Miniforge from https://github.com/conda-forge/miniforge/releases
conda create -n securetap -c conda-forge python=3.13 dlib -y
conda activate securetap
# (if `conda activate` isn't recognized in a new terminal, run `conda init powershell`
#  once and restart the terminal - or just call that env's python.exe directly, e.g.
#  C:\Users\<you>\miniforge3\envs\securetap\python.exe)

cd backend
pip install -r requirements.txt
copy .env.example .env
# edit .env: set DB_PASSWORD to the password you used in docs/create_database.sql,
# generate DJANGO_SECRET_KEY and ENTRY_AGENT_SERVICE_TOKEN, e.g.:
#   python -c "import secrets; print(secrets.token_urlsafe(50))"

python manage.py migrate
python manage.py seed_dummy_data
python manage.py runserver
```

Backend runs at `http://localhost:8000`. Django admin at `http://localhost:8000/admin` (log in with a seeded admin account below, or `python manage.py createsuperuser`).

Seeded demo dashboard logins (from `seed_dummy_data`) - **change these passwords before any real deployment**:

| Username | Password | Role |
|---|---|---|
| `admin_demo` | `ChangeMe123!` | admin |
| `security_demo` | `ChangeMe123!` | security |
| `it_demo` | `ChangeMe123!` | it |

The seeded dummy *people* (students/staff) have **random, fake face encodings** - they're only useful for exercising the logs/reports/listing UI, not real face matching. Register at least one real person with a real photo (dashboard's Users page, `POST /api/users`, or Django admin - all three now compute a real encoding on save) so the continuous scan has someone real to recognize.

## 3. Entry-agent setup (Windows, plain Python - no conda needed)

The entry-agent doesn't run face recognition itself - it captures frames and posts them to the backend, which does the actual matching. So it stays on the plain system Python; no dlib, no conda required here.

Two things happen concurrently once it's running:
- **Continuous scan** (primary): every ~2 seconds it grabs a webcam frame and posts it to `/api/identify`, which searches every enrolled face for a match. A recognized person shows a green check + name; an unrecognized face shows a red X ("not enrolled"); a person recognized again within the cooldown window (`RECOGNITION_COOLDOWN_SECONDS`, default 30s) doesn't spam a new log row.
- **Card tap** (optional/secondary): posts to `/api/verify`, a lookup only - no fresh photo is taken. On a match it fetches and displays that person's on-file photo + name for the guard to eyeball-compare; on no match it shows an error. This is the only flow that uses the offline queue (`entry-agent/offline_queue.db`), since a discrete tap is worth preserving for the audit trail even if the guard can't see the photo until later - the continuous scan doesn't queue anything, since a stale frame has no value once connectivity returns.

**About the NFC reader:** cheap "13.56MHz IC / 125KHz ID" combo readers (and many similar low-cost USB RFID modules) are **not** PC/SC smart-card devices - they're USB HID-keyboard-emulation devices. Tapping a card literally "types" the card's ID followed by Enter into whatever window currently has keyboard focus, exactly like a very fast typist. Because of this:

- The entry-agent window keeps a hidden, always-focused input to catch that typing - **keep its window focused/on top** (it's already set to stay on top) rather than clicking into some other application while it's running.
- There's no separate reader-detection step and no Windows service dependency for this - if it types into Notepad, it'll type into the entry-agent.
- **Tip:** to find out what ID a given card produces (e.g. when registering a new person), click into the "NFC ID" field on the dashboard's Add User form and tap the card there - it types the ID directly into that field.
- If you have a genuine PC/SC reader instead (e.g. an actual ACR122U), this approach won't see it; that would need reintroducing `pyscard` and reading via APDU commands instead.

```powershell
cd entry-agent
pip install -r requirements.txt
copy .env.example .env
# SERVICE_TOKEN must match ENTRY_AGENT_SERVICE_TOKEN in backend/.env
python main.py
```

Requirements:
- A webcam (used continuously - keep it unobstructed and aimed at the gate)
- The USB NFC/RFID reader plugged in, optional (no driver install needed - Windows sees it as a generic keyboard)

## 4. Dashboard setup (React + Vite + Tailwind)

```powershell
cd dashboard
npm install
copy .env.example .env
npm run dev
```

Dashboard runs at `http://localhost:5173`.

## Running everything together

1. MySQL running (Windows service).
2. `cd backend`, `conda activate securetap`, `python manage.py runserver`.
3. `cd dashboard`, `npm run dev`.
4. `cd entry-agent`, `python main.py`.
5. Log into the dashboard and register a real person via the Users page (needs one clear, front-facing photo with a single face in it). The entry-agent should recognize them automatically within a couple of seconds of facing the webcam - no tap needed. Optionally tap their NFC card too and confirm their photo pops up on the entry-agent window. Both should appear on the dashboard's Live Monitoring page.

## Data privacy notice

This system processes **biometric data** (face encodings, reference photos) on **everyone who passes the gate**, not just people who choose to tap a card - the continuous camera scan identifies every face it sees, including visitors and anyone else in view. This is a meaningfully bigger privacy footprint than a purely tap-triggered check, and falls under the **Philippine Data Privacy Act of 2012 (RA 10173)**. Before deploying this at an actual gate:

- Collect explicit written consent for biometric data collection and processing from every student/staff member being enrolled.
- Post clear, visible notice at the gate that facial recognition is in continuous operation, covering anyone who passes through - not just enrolled students/staff.
- Explain how the data will be used, stored, retained, and for how long.
- Provide a documented way to request correction or deletion.
- Confirm this broader always-on scanning scope (versus a consent-gated tap-triggered check) is something the institution's data protection officer/process has actually signed off on.

The system stores face **encodings** (128-dimensional vectors), not raw images, in the database specifically to reduce biometric exposure. Reference photos are kept separately (`backend/media/reference_photos/`) only for dashboard/guard display, and should be access-controlled in any real deployment.

## Architecture notes

- **Continuous 1:N face identification** (`POST /api/identify`) is the primary gate check - it compares a sampled frame against every enrolled active person's encoding (a linear scan via `face_recognition.face_distance`, fast enough for hundreds of enrolled people, not built to scale to a huge student body). A match within `RECOGNITION_COOLDOWN_SECONDS` of the same person's last recognition reuses the existing log row instead of creating a new one.
- **`POST /api/verify`** is now a plain NFC lookup (no image) used only by the optional card-tap flow - it returns the matched person's name and on-file photo URL for the guard to see, or an error if the card isn't linked to an enrolled, active person.
- **Real-time updates** use short-interval polling (`GET /api/logs/live`) rather than WebSockets/Django Channels - simpler to run reliably without adding Redis + an ASGI server.
- **`admin_accounts`** is implemented as Django's built-in `User` model plus an `AdminProfile(role)` model (see `backend/accounts/`), with `bcrypt` configured as the primary password hasher - not a hand-rolled auth table.
- The entry-agent authenticates to `/api/verify` and `/api/identify` with a shared-secret `X-Service-Token` header, not a JWT - it's a trusted device, not a logged-in dashboard user.
- **Bulk import** (`POST /api/users/bulk-import`) matches photos already placed on the server at `backend/seed_data/photos/<student_or_employee_id>.jpg` by filename. Registering a person - via the API, the dashboard, or Django admin - always computes a real face encoding from the photo on save.
- The entry-agent queues NFC tap lookups (not scan frames) in a local SQLite file (`entry-agent/offline_queue.db`) when the backend is unreachable, and a background thread syncs them automatically once connectivity returns.
- The NFC reader is read as HID-keyboard-emulation input (a focused, off-screen Tkinter `Entry` widget) rather than through `pyscard`/PC-SC - see the note in step 3. The continuous scan and each tap lookup run on their own worker threads so camera/network I/O never freezes the feedback window.
