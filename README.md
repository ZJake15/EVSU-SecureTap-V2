# EVSU SecureTap

Campus entry monitoring system for Eastern Visayas State University, with a real-time admin dashboard.

**How the gate check works:** the webcam continuously scans everyone passing the gate (like a CCTV feed) and checks each face against every enrolled student/staff photo (a 1:N identification search, via InsightFace/ArcFace) - no card tap required. A match isn't trusted from a single frame: the same person has to be the top match across several recent frames before it's logged as a real entry, and a blurry or partially-out-of-frame frame is skipped rather than risked. The NFC card reader is a secondary, optional channel - normally it's just a lookup for the guard to visually cross-check, but if the camera sees a borderline/ambiguous match (or a match against someone flagged as a **confusable pair** - identical twins, or any other genuine lookalike the camera can't be expected to tell apart) it will ask for a confirming card tap to break the tie. Recognized people are logged automatically; an unrecognized face is flagged as not enrolled.

**For the full architecture, role/permission model, and design rationale behind every decision above, see [`documentation.md`](documentation.md).**

## Project layout

```
evsu-securetap/
├── SecureTap.bat  # double-click this - opens the launcher
├── launcher.py    # one menu: starts the backend, opens the dashboard or entry-agent
├── setup_wizard.py   # first-run setup window (files, data import, first Admin, speed test)
├── device_setup.py   # this computer's speed mode + setup helpers
├── backend/       # Django + DRF API (accounts, users, logs, reports apps) + SQLite database; also serves the dashboard
├── entry-agent/   # Windows app: webcam capture + NFC card read -> calls the API
├── dashboard/     # React + Tailwind admin dashboard
├── docs/          # SQL snippets, supplementary docs, UI redesign mockups + design brief
└── README.md
```

## Prerequisites

- Windows 10/11
- **No database server to install** - the database is SQLite, a single file the backend creates itself. (MySQL is still supported as an option - see "Using MySQL instead" below.)
- Node.js 20 LTS or newer + npm - **only to build the dashboard** (once, and again after changing its code). Running SecureTap doesn't need it: the backend serves the built dashboard.
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

Nothing to install. The database is **SQLite** - one file, `backend/db.sqlite3`, created by `python manage.py migrate` in the next step. (`DB_ENGINE=sqlite` in `backend/.env`; `DB_PATH` can put the file elsewhere.) It's never committed to git - it holds face data and password hashes.

### Using MySQL instead (optional)

For a real deployment with a separate database server: install MySQL 8.0+, `pip install -r backend\requirements-mysql.txt`, open `docs/create_database.sql` and replace `CHANGE_ME_TO_A_STRONG_PASSWORD` with a password you choose, run it as root (`mysql -u root -p < docs/create_database.sql`, or paste it into MySQL Workbench/HeidiSQL), then in `backend/.env` set `DB_ENGINE=mysql` and fill in the `DB_*` lines. Also load MySQL's time-zone tables (`mysql_tzinfo_to_sql`) - without them, filtering logs by date returns nothing. Moving an existing MySQL database into SQLite: `python manage.py copy_mysql_to_sqlite` (MySQL is only read; it stays as a backup), then set `DB_ENGINE=sqlite`.

## 3. Backend setup (Django)

**One** virtual environment at the **repo root** serves both the backend and the entry-agent. They share most of their dependencies (Pillow, NumPy, OpenCV), and `SecureTap.bat` looks for exactly one interpreter at `.venv\Scripts\python.exe`.

```powershell
# from the repo root - not inside backend/
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
pip install -r backend\requirements.txt --only-binary=:all:
pip install -r entry-agent\requirements.txt
# Only when bringing data over from an older copy that used MySQL:
pip install -r backend\requirements-mysql.txt
```

That's all the typing. The first time you double-click `SecureTap.bat`, its **setup window** creates `backend\.env` and `entry-agent\.env` with fresh secrets, creates the database (or brings it over from an older copy), asks for the first Admin account and runs the speed test - see "First-time setup" below.

Prefer doing it by hand? `copy backend\.env.example backend\.env` (fill in the secrets - see "Getting your secrets"), then `cd backend`, `python manage.py migrate`, and `python manage.py create_first_admin --username <name>` (it asks for the password). Optional demo data: `python manage.py seed_dummy_data` - fake people plus three demo logins with a **known password**, so never on a real deployment.

You don't need to start the server by hand - `SecureTap.bat` does that (see "Running everything together"). To run it manually anyway: `cd backend`, `python manage.py runserver`.

Backend runs at `http://localhost:8000`. Django admin at `http://localhost:8000/admin` (log in with a seeded admin account below, or `python manage.py createsuperuser`).

**First run only:** the first request that touches face recognition (enrolling someone, or the entry-agent's first scan) downloads the InsightFace `buffalo_s` model (~125MB) automatically, cached afterward under `%USERPROFILE%\.insightface\models\`. Needs internet access the first time; instant after that. (`buffalo_s` was chosen over the larger `buffalo_l` for CPU speed on low-power hardware with no GPU - see `insightface_utils.py`. If you ever switch models, run `python manage.py recompute_embeddings` afterward or existing enrollments won't match anymore.)

Seeded demo dashboard logins (only if you ran `seed_dummy_data`) - **change these passwords before any real deployment**:

| Username | Password | Role | Notes |
|---|---|---|---|
| `admin_demo` | `ChangeMe123!` | Admin | Full access - see `documentation.md` §13 for the full permission matrix |
| `saso_demo` | `ChangeMe123!` | SASO (Security Manager) | Full enroll/edit; deactivating a record needs Admin approval (maker-checker) |
| `guard_demo` | `ChangeMe123!` | Security Officer | Assigned to "Main Gate" - Live Monitoring/Logs are scoped to that gate only, no User Management/Reports access at all |

The seeded dummy *people* (students/staff) have **random, fake face embeddings** - they're only useful for exercising the logs/reports/listing UI, not real face matching. Register at least one real person with real photos via the dashboard's Users page (guided 5-photo capture, or the single-photo fallback) so the continuous scan has someone real to recognize.

## 4. Entry-agent setup (Windows, plain Python)

The entry-agent doesn't run face recognition itself - it captures frames and posts them to the backend, which does the actual matching, so it needs no special ML dependencies. Its Python packages already went into the shared root venv in step 3, so all that's left here is its own `.env`:

```powershell
# Only if you're not using the setup window, which does this for you:
copy entry-agent\.env.example entry-agent\.env
# SERVICE_TOKEN must match ENTRY_AGENT_SERVICE_TOKEN in backend/.env exactly.
```

Open it from `SecureTap.bat` (see "Running everything together"). To run it manually anyway: `cd entry-agent`, `python main.py`.

Picking **Entry Agent** in the launcher opens the **gate monitor** directly - no second menu to click through. It's a single, maximized window handling both credentials at once: on the left, a stats row (Today / Entries / Unknown / Spoof / In frame) above the camera feed and a compact card-scanner strip; on the right, the live log running the full height of the window; and a status bar along the bottom carrying camera/backend/queue state. It's sized to fit your screen, so it keeps the same layout whatever Windows' display-scaling setting is. Closing it exits the entry-agent. The **Open student display** button in its status bar opens a second, student-facing screen - a mirrored camera view with big plain labels ("Welcome, Maria", "Please uncover your face", "Tap your ID card") and a message bar telling each person what to do; with a second monitor connected it goes fullscreen there (F11 toggles fullscreen). Two things happen while it's running:
- **Continuous scan** (primary): every ~0.2s it grabs a webcam frame and posts it to `/api/identify`. A match needs to agree across several recent frames before it's confirmed (not a single frame); a blurry or partially-out-of-frame face is skipped and shown as "Checking..." rather than risked as a false "Unknown". A recognized person shows a green box + name + confidence; a confirmed unrecognized face shows an amber "Unknown" box plus an alarm and an UNKNOWN PERSON banner across the top of the feed (a spoof gets a red one); a covered face shows a blue "Please uncover your face" label (no banner, no alarm, and it isn't logged); a borderline/ambiguous match shows a blue "Tap card to confirm" prompt.
- **Card tap** (secondary): posts to `/api/verify` - normally just a lookup that shows the guard the tapped person's on-file photo for a visual cross-check, but if the camera currently has a pending tiebreak for this gate, the tap resolves that instead. Either way, a successful tap is logged as a real gate entry (deduped if the same card is tapped again within the cooldown window) and appears in Live Monitoring/Logs like a face-scan entry does.

Both land in the same live log on the right of the monitor - the newest event as a large card, earlier ones listed below it - with face events marked `ENTRY`/`EXIT`/`UNKNOWN`/`SPOOF` and card taps `CARD · ENTRY` or `CARD REJECTED`, so the log is one chronological record of the gate no matter which credential was used.

**About the NFC reader:** cheap "13.56MHz IC / 125KHz ID" combo readers (and many similar low-cost USB RFID modules) are **not** PC/SC smart-card devices - they're USB HID-keyboard-emulation devices. Tapping a card literally "types" the card's ID followed by Enter into whatever window currently has keyboard focus, exactly like a very fast typist. Because of this:

- The entry-agent's gate-monitor window keeps a hidden, always-focused input to catch that typing - **keep its window focused/on top** rather than clicking into some other application while it's running.
- There's no separate reader-detection step and no Windows service dependency for this - if it types into Notepad, it'll type into the entry-agent.
- The launcher does warn before opening the gate monitor if it can't find a known reader by USB ID (a genuine ACR122U, `072F:2200`, or the unbranded reader this gate uses, `FFFF:0035`). If yours is plugged in but still reported missing, add its `VID:PID` (Device Manager → the reader → Properties → Details → Hardware Ids) to `NFC_READER_USB_IDS` in `entry-agent/.env`.
- **Tip:** to find out what ID a given card produces (e.g. when registering a new person), click into the "NFC ID" field on the dashboard's Add User form and tap the card there - it types the ID directly into that field.
- If you have a genuine PC/SC reader instead (e.g. an actual ACR122U), this approach won't see it; that would need reintroducing `pyscard` and reading via APDU commands instead.

Requirements:
- A webcam, used continuously - keep it unobstructed and aimed at the gate. **Not strictly required to start the app**: with none connected (or one that's unplugged mid-session), the gate monitor still opens and shows "No camera connected" in the video panel instead of freezing - NFC taps and a Security Officer's manual override still work normally. It reconnects automatically on its own timer, whether the camera shows up for the first time or was unplugged and plugged back in - no restart needed.
- The USB NFC/RFID reader plugged in, optional (no driver install needed - Windows sees it as a generic keyboard)
- **Which camera:** each time the gate monitor opens, it uses a **plugged-in (USB) camera before the laptop's built-in one** (`entry-agent/camera_select.py`). Windows' own device information tells them apart - everything built into the computer belongs to the computer's "machine container"; a plugged-in camera doesn't - and the camera's name is the fallback ("Integrated Camera"). Software cameras (OBS, LSVCam) and infrared face-sign-in cameras come last. The camera list in the gate monitor's status bar shows each camera's kind; picking one there switches now and is remembered **by name** (`camera_choice.json`, since camera numbers change whenever a camera is plugged in or out) to choose between cameras of the same kind - a plugged-in camera still wins over a remembered built-in one. `CAMERA_INDEX` only matters if no camera can be listed.
- Optional/experimental: `CAMERA_EXPOSURE` in `entry-agent/.env` can force a shorter exposure to reduce motion blur, but manual-exposure support varies a lot by webcam/driver - leave it unset unless you've confirmed it works on your specific camera (see the comment in `entry-agent/camera.py`).

## 5. Dashboard setup (React + Vite + Tailwind)

```powershell
cd dashboard
npm install
npm run build
```

That builds the dashboard into `dashboard/dist`, which **the backend serves at `http://localhost:8000/`** - no separate dashboard server. The launcher rebuilds it by itself whenever the dashboard's code has changed since the last build.

Editing the dashboard? `npm run dev` still works (live reload at `http://localhost:5173`); it forwards `/api` and `/media` to the backend on port 8000.

The dashboard loads its fonts (Archivo, Atkinson Hyperlegible Next, IBM Plex Mono) from Google Fonts, so it needs internet access to look as designed; offline it still works, just in the browser's default fonts. Its icons come from the `@phosphor-icons/web` npm package and work offline. The entry-agent and launcher don't need internet for this - their fonts are bundled in `entry-agent/assets/fonts/`. See `documentation.md` §8.1 for the design system.

## Getting your secrets

The setup window generates these by itself on a new computer - this section is for filling them in by hand. Three values need to be filled in before anything will run - none of them are things you "look up" anywhere, they're generated or chosen locally:

### `DB_PASSWORD` (`backend/.env`) - only with MySQL
Not needed with the default SQLite database. With `DB_ENGINE=mysql`, **you choose it**: pick a strong password, put it in `docs/create_database.sql`'s `CREATE USER ... IDENTIFIED BY '...'` line (replacing the placeholder), run that script, then put the exact same password in `backend/.env`'s `DB_PASSWORD`. To generate a random one instead of making one up:
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

## Installing on another computer (one Setup.exe)

For a computer that just needs to **run** SecureTap - like the presentation laptop - there's an installer: no Python, Node.js, database or internet needed there.

**Build it** (on this development computer, with everything above set up, and Inno Setup 6: `winget install --id JRSoftware.InnoSetup -e --scope user`):

```powershell
.venv\Scripts\python.exe installer\build.py           # about 10 minutes the first time
.venv\Scripts\python.exe installer\build.py --quick   # after only SecureTap's own code changed
```

It produces `installer\output\EVSU-SecureTap-Setup.exe` (about 170 MB). Inside: SecureTap's own copy of Python with **exactly the library versions this computer runs** (pinned from the venv - not whatever is newest), MySQL support for importing an older copy, Microsoft's C++ runtime, the dashboard already built, and the two face models (so no internet on first use). Only files tracked in git go in, so no database, `.env`, photos or trained model can end up in it.

**Install it:** copy the file to the other computer (USB is fine) and double-click it. Windows may show "Windows protected your PC" because the file isn't signed - click **More info → Run anyway**. The installer's first two pages are the **terms of service** (`installer/TERMS-OF-SERVICE.txt` - who may use it, permitted and forbidden uses, account and gate duties, the face models' non-commercial license, no warranty, Philippine law) and the **privacy policy** (`installer/PRIVACY-POLICY.txt` - what is collected and not collected, why, where it's kept, who can see it, sharing, retention, consent, minors, rights under RA 10173, breaches), and both have to be accepted before installing; copies are installed with the app (Start menu → *EVSU SecureTap Terms of Service* / *EVSU SecureTap Privacy Policy*). Have the university's Data Protection Officer review both before any real deployment. It installs for that Windows user only, without asking for an admin password, into `%LOCALAPPDATA%\Programs\EVSU SecureTap`, with **EVSU SecureTap** on the desktop and in the Start menu. Opening it the first time starts the setup window (below) - which is where the data from an older copy is brought over. Installing a newer version over it keeps the data; uninstalling (Settings → Apps) asks before deleting the data.

**Tested on a brand-new Windows:** `.venv\Scripts\python.exe installer\sandbox_test.py` installs it in Windows Sandbox with the internet switched off and checks 14 things - the files, database and first Admin, the backend serving the dashboard and a login, the face-AI speed test, the gate monitor and launcher code, nothing written into the program folder, the desktop icon. All 14 pass. (Windows Sandbox needs turning on once: "Turn Windows features on or off" → Windows Sandbox, restart. If it then still won't start, the boot setting `hypervisorlaunchtype` is off - `bcdedit /set {current} hypervisorlaunchtype auto` in an admin terminal, restart; Android emulators/VirtualBox may run slower with it on.)

**Try it yourself in the Sandbox:** `.venv\Scripts\python.exe installer\sandbox_test.py --try` (close any open Sandbox first - only one runs at a time). It opens a fresh Sandbox with a **SecureTap installer** folder on its desktop, your webcam shared, 8 GB of memory like the presentation laptop and no internet. Run `EVSU-SecureTap-Setup.exe` there (More info → Run anyway), go through the setup window with **Start empty** (there's no older copy inside the Sandbox), then enroll yourself on the dashboard and open the gate monitor. Limits: the NFC card reader doesn't work there (USB devices aren't passed in), it's a bit slower than a real computer, Windows may ask to let Windows Sandbox use the camera - and closing the Sandbox deletes everything in it.

### Putting it on the demo laptop

The laptop already has an older SecureTap copy with the students enrolled (on MySQL); the installed app brings that data over and the old copy stays untouched as a backup. Do it a few days before the defense, logged into the **Windows account you'll use on the day** (the app installs per user).

1. **Fresh installer:** if any code changed since the last build, run `installer\build.py --quick` first. Copy `installer\output\EVSU-SecureTap-Setup.exe` to the laptop (USB is fine).
2. **Close the old SecureTap completely** (launcher, gate monitor, any backend window) - both use port 8000.
3. **MySQL running:** Services → **MySQL80** says Running (the old copy's students are in it; its password is read from the old copy's own `backend\.env`).
4. **Install:** double-click the Setup.exe (More info → Run anyway), keep "Open EVSU SecureTap now" ticked.
5. **Setup window:** **Bring the data from an older copy** - it usually finds the old folder by itself, otherwise **Choose folder…**. People, faces, records, accounts (same usernames and passwords), settings, photos, the covered-face model and the gate's name, camera and card-reader settings come along, and it checks every table and that faces still match their photos. The Admin step is skipped (the accounts came along); the camera and card reader should be found; the speed test should pick **Light** on the N100.
6. **Check it:** Dashboard → log in, the people and logs are there; Entry Agent → an enrolled student is recognized, a card tap works; close and reopen from the desktop icon.

Afterwards use only the new desktop icon - don't run the old `SecureTap.bat` at the same time. MySQL isn't needed any more: optionally set **MySQL80** to Manual and stop it to free memory on the 8 GB laptop (keep its data as the backup). After a later code change, build a new Setup.exe and install it over this one - the data stays. If the import stops with an error, the setup window says why (for example MySQL not running); nothing has been changed, so fix it and try again.

## First-time setup

The first time `SecureTap.bat` opens on a computer, a setup window comes first (and again from the launcher's **Speed mode → Run setup again**). Each step that's already done is just confirmed:

1. **Welcome** - creates `backend\.env` and `entry-agent\.env` from their templates, with a fresh secret key and a gate key written into both, so they match.
2. **Your data** - start empty, keep the data that's already here, or **bring the data from an older SecureTap copy** on this computer (for example the one already set up on the presentation laptop). Point it at that copy's folder - it's found by itself when it sits next to this one or in Documents/Desktop/Downloads. Everything comes along: people and their faces, entry records, accounts (same usernames and passwords), settings, the audit log, the photos, the trained covered-face model, and the gate's own settings (gate name, direction, camera, card reader, guard name). Nobody has to be registered again. The old copy is only read, never changed - it can be MySQL (its MySQL service has to be running, and this copy needs `requirements-mysql.txt`) or SQLite. Afterwards it checks that every table arrived complete and that the faces still match their photos.
3. **Admin account** - if there isn't one yet, you type the first one in (no default password exists). Further accounts are made on the dashboard.
4. **Camera and card reader** - a quick check; it never stops you. It shows which camera the gate monitor will use: a **plugged-in (USB) camera always comes before the laptop's built-in one** (see "Which camera" below).
5. **Speed test** - about half a minute: it times the gate's face check on this computer and picks a **speed mode** (below). You can pick a different one right there.

Command-line equivalents: `python manage.py import_securetap "C:\path\to\old\copy"` (add `--replace` if this copy already has data - it's kept as `db.sqlite3.bak`), `python manage.py create_first_admin --username <name>`, `python manage.py benchmark_scan --sample`.

### Where the data is kept

Running from this code folder (as in the steps above), everything stays where it always was: `backend\.env`, `backend\db.sqlite3`, `backend\media\`, `entry-agent\.env` and so on. An **installed** copy (from the installer, which marks it with `securetap-installed.txt`) never writes into its program folder - everything goes to one data folder, `%LOCALAPPDATA%\EVSU SecureTap\` (`backend.env`, `entry-agent.env`, `db.sqlite3`, `media\`, the covered-face model, the card-tap queue, the speed mode and the launcher's settings), so reinstalling or updating never touches the students' data. `SECURETAP_DATA_DIR` points it somewhere else.

### Speed modes

| Mode | What changes | Picked when |
|---|---|---|
| **Fast** | Smoother camera view (20 fps), quicker face checks | each face check is under 35 ms on a computer with 8+ cores and 8+ GB |
| **Standard** | Nothing - the normal settings | in between |
| **Light** | Lighter video (12 fps), smaller frames, a slightly longer pause between checks, smaller face-search size - faces need to be within about 2-3 m of the camera | a face check takes 90 ms or more, **or** the computer has 4 cores or fewer (like the Intel N100), **or** under 6 GB of memory |

The mode is saved in `device_profile.json` (per computer, not in git) and handed to the backend and the gate monitor each time the launcher starts them - the `.env` files aren't rewritten. Change it, or re-run the speed test, under **Speed mode** in the launcher: the backend restarts by itself when it needs to; reopen the gate monitor if it's open.

## Running everything together

**Double-click `SecureTap.bat`.** That's it - no terminal commands.

It opens one window that starts the Django backend for you (its status bar at the top shows Backend / Dashboard / Entry Agent as Ready, Running, Starting…, Not running or Failed) and then asks the only question that's genuinely a choice:

- **Dashboard** - opens your browser at `http://localhost:8000/`, served by the backend (rebuilding the dashboard first only if its code changed since the last build).
- **Entry Agent** - opens the gate monitor (camera + NFC) in its own window.

The backend isn't a third button because it isn't a choice - both front ends are useless without it, so it just starts.

Worth knowing:

- **No database server to start** - the SQLite database is just a file the backend opens. (With the optional `DB_ENGINE=mysql`, the MySQL service has to be running.)
- **Already have the backend running?** If `runserver` is already up in a terminal, the launcher detects that and uses it instead of starting a duplicate that would die on "port already in use" - and it won't kill it when it quits.
- **Show log** reveals the merged output of everything it started. That's where a backend that failed to start explains itself (bad `.env`, port taken).
- **Speed mode** shows this computer's mode (Fast / Standard / Light) - switch it, re-run the speed test, or run the first-time setup again (for example to bring data over from another copy later).
- **Entry Agent settings** (gate, direction, guard name, auto-open) are remembered between launches. Before opening the gate monitor it also checks that the backend answers and that the NFC reader is plugged in; if either looks wrong you get a warning with **Open gate monitor** or **Cancel** - it never blocks you on its own.
- **Quitting stops everything it started**, and asks first. Anything it merely adopted is left alone.
- `npm install` and the first `npm run build` in `dashboard/` are one-time steps (only on a computer that builds the dashboard); the launcher says so plainly if the dashboard hasn't been built.

Equivalent manual commands, if you prefer them or you're on a machine without the `.bat`: `python manage.py runserver` in `backend/` (then open `http://localhost:8000/`), `python main.py` in `entry-agent/` - all using the shared root venv.

Then: log into the dashboard and register a real person via the Users page - guided capture walks through 5 near-frontal shots (front, slight left/right turn, neutral, smile), or use the single-photo fallback if you're staging one for bulk import instead. The entry-agent should recognize them automatically within a few seconds of facing the webcam, after enough frames agree - no tap needed. Optionally tap their NFC card too and confirm their photo pops up on the entry-agent window. Both should appear on the dashboard's Live Monitoring page and in Logs.

**Settings page (Admin only):** match strictness, the fake-face check, card-tap rules, photo-quality checks, data retention, login lockout, automatic logout and gate alerts are all changed from the dashboard's **Settings** page - no `.env` editing or restarts; changes reach every gate within a few seconds and each one is recorded in the Audit Log. Risky changes need confirming. The values in `backend/.env` are only each setting's starting value. New features (automatic deletion, lockout, automatic logout, repeated-unknown alerts, guard sign-in) start switched off. See documentation.md section 8.2.

**Guard sign-in at the gate monitor (optional):** switch on **Guards sign in at the gate monitor** (Settings → Security & Accounts). The gate monitor's status bar then shows who's on duty; a guard signs in with their own dashboard username and password, or by tapping their own staff ID card (put it on their account once: Accounts page → **Staff ID card** → tap the card into the box). A Security Officer can sign in only at their assigned gate; an Admin or SASO at any gate. Tapping another staff card hands over the shift; closing the gate monitor signs the guard out. The gate keeps scanning when nobody is signed in - those entries show as **Unattended** in Logs, and every other entry shows who was on duty. See documentation.md section 13.4.

## Data privacy notice

This system processes **biometric data** (face embeddings, reference photos) on **everyone who passes the gate**, not just people who choose to tap a card - the continuous camera scan identifies every face it sees, including visitors and anyone else in view. This is a meaningfully bigger privacy footprint than a purely tap-triggered check, and falls under the **Philippine Data Privacy Act of 2012 (RA 10173)**. Before deploying this at an actual gate:

- Collect explicit written consent for biometric data collection and processing from every student/staff member being enrolled.
- Post clear, visible notice at the gate that facial recognition is in continuous operation, covering anyone who passes through - not just enrolled students/staff.
- Explain how the data will be used, stored, retained, and for how long - and set those periods under Settings → Privacy & Data Retention, then switch on Automatic deletion (it's off until you do, so nothing is ever deleted by default).
- Provide a documented way to request correction or deletion.
- Confirm this broader always-on scanning scope (versus a consent-gated tap-triggered check) is something the institution's data protection officer/process has actually signed off on.

The system stores face **embeddings** (512-dimensional ArcFace vectors), not raw images, in the database specifically to reduce biometric exposure. Reference/enrollment photos are kept separately (`backend/media/`) only for dashboard/guard display and re-computing embeddings if the model ever changes, and should be access-controlled in any real deployment. Photo files are deleted together with their records (permanently deleting a person deletes all their photos; replacing a photo deletes the old one), and the daily clean-up always removes photos no record uses and the face data of unrecognized people after 1 day - even with Automatic deletion off. The full privacy policy shown by the installer is `installer/PRIVACY-POLICY.txt`.

## Architecture notes

- **Continuous 1:N face identification** (`POST /api/identify`) is the primary gate check - it compares a sampled frame's embedding against every enrolled active person's stored embeddings (a vectorized cosine-similarity scan, fast enough for hundreds of enrolled people/embeddings, not built to scale to a huge student body without a proper vector index like FAISS). A single frame is never trusted alone: the same person has to be the top match across several recent scans (a rolling-window majority vote) before a real log row is written, and the same grace period applies to "Unknown" so a single blurry frame can't falsely flag someone. A borderline or genuinely ambiguous match (two similar-looking candidates) triggers an NFC tiebreak instead of guessing.
- **`POST /api/verify`** handles a card tap - normally a lookup that returns the matched person's profile for the guard to visually cross-check, but resolves an active NFC tiebreak if one is pending for that gate. A successful tap logs a real gate entry (deduped against a recent tap of the same person). The endpoint still accepts a `student_or_employee_id` instead of an `nfc_id`, but nothing sends it - the entry-agent's typed-ID fallback was removed, so a tap is the only way in.
- **Real-time updates** use short-interval polling (`GET /api/logs/live`) rather than WebSockets/Django Channels - simpler to run reliably without adding Redis + an ASGI server.
- **`admin_accounts`** is implemented as Django's built-in `User` model plus an `AdminProfile(role)` model (see `backend/accounts/`), with `bcrypt` configured as the primary password hasher - not a hand-rolled auth table.
- The entry-agent authenticates to `/api/verify` and `/api/identify` with a shared-secret `X-Service-Token` header, not a JWT - it's a trusted device, not a logged-in dashboard user.
- **Bulk import** (`POST /api/users/bulk-import`) matches photos already placed on the server at `backend/seed_data/photos/<student_or_employee_id>.jpg` by filename, and flags the resulting single-photo enrollment as lower-confidence in the data.
- **Photo uploads are restricted to JPEG, PNG and HEIC** (`.jpg/.jpeg/.png/.heic/.heif`) - the enrollment photo, every guided-capture slot, extra photos, and the profile picture. HEIC is included because iPhones save that way by default. The backend checks the file's *actual decoded format* via Pillow, not its extension or `Content-Type`, so a renamed `.webp` is still rejected - which matters here because `pillow-heif` also teaches Pillow to read **AVIF**, and AVIF is deliberately *not* accepted. Note that no non-Apple browser can render a HEIC, so the dashboard shows a "HEIC - no preview" placeholder before upload; the file itself uploads and is stored re-encoded as JPEG, so it displays normally afterwards.
- The entry-agent queues NFC tap lookups (not scan frames) in a local SQLite file (`entry-agent/offline_queue.db`) when the backend is unreachable, and a background thread syncs them automatically once connectivity returns.
- The NFC reader is read as HID-keyboard-emulation input (a focused, off-screen Tkinter `Entry` widget) rather than through `pyscard`/PC-SC - see the note in the entry-agent section above. The continuous scan and each tap lookup run on their own worker threads, pushing updates to the gate monitor through a thread-safe queue, so camera/network I/O never freezes the UI.
