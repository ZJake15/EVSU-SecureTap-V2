"""Runs INSIDE Windows Sandbox, right after a silent install - see
installer/sandbox_test.py. Checks the installed copy works on a brand-new
Windows with no internet: files, database, an Admin, the backend serving the
dashboard and a login, the speed test (the face AI, from the bundled
models), the gate monitor's and the launcher's code, and that nothing was
written into the program folder. Writes results.json to C:\\results."""

import json
import os
import subprocess
import sys
import time
import traceback
import urllib.request
from pathlib import Path

RESULTS = Path(r"C:\results")
APP = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "EVSU SecureTap"
PYTHON = APP / "python" / "python.exe"
PASSWORD = "Sandbox-Check-2026"
results = {}


def record(name, ok, detail=""):
    results[name] = {"ok": bool(ok), "detail": str(detail)[:500]}
    print(("PASS " if ok else "FAIL ") + name + (f" - {detail}" if detail else ""), flush=True)


def program_files():
    return {str(p.relative_to(APP)) for p in APP.rglob("*") if p.is_file() and "__pycache__" not in p.parts}


def manage(*args, stdin=None):
    result = subprocess.run([PYTHON, "manage.py", *args], cwd=APP / "backend", input=stdin, capture_output=True,
                            text=True, encoding="utf-8", errors="replace")
    return result.returncode, (result.stdout + result.stderr).strip()


def http(path, data=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(f"http://127.0.0.1:8000{path}", headers=headers,
                                     data=json.dumps(data).encode() if data is not None else None)
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.status, response.read()


def main():
    record("installed", (APP / "launcher.py").exists() and (APP / "securetap-installed.txt").exists(), APP)
    before = program_files()
    sys.path.insert(0, str(APP))
    sys.path.insert(0, str(APP / "entry-agent"))
    os.chdir(APP)
    import device_setup

    expected = Path(os.environ["LOCALAPPDATA"]) / "EVSU SecureTap"
    record("data folder", device_setup.DATA_DIR == expected, device_setup.DATA_DIR)
    record("settings files created", len(device_setup.create_env_files()) == 2 and device_setup.gate_key_matches())

    code, output = manage("migrate")
    record("database created", code == 0, output.splitlines()[-1] if output else "")
    code, output = manage("create_first_admin", "--username", "sandboxadmin", stdin=PASSWORD + "\n")
    record("first Admin created", code == 0, output.splitlines()[-1] if output else "")

    backend = subprocess.Popen([PYTHON, "manage.py", "runserver", "127.0.0.1:8000", "--noreload"],
                               cwd=APP / "backend", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        up = False
        for _ in range(90):
            try:
                up = http("/api/health")[0] == 200
                break
            except OSError:
                time.sleep(1)
        record("backend starts", up)
        status, body = http("/")
        record("dashboard served", status == 200 and b'id="root"' in body, f"HTTP {status}")
        status, body = http("/api/auth/login", {"username": "sandboxadmin", "password": PASSWORD})
        token = json.loads(body).get("access")
        record("Admin can log in", status == 200 and token)
        status, _body = http("/api/settings", token=token)
        record("Settings page data loads", status == 200, f"HTTP {status}")
    finally:
        backend.terminate()
        backend.wait(15)

    try:
        measured = device_setup.run_speed_test(str(PYTHON))
        mode, reason = device_setup.recommend(measured)
        record("speed test (face AI, offline)", measured.get("faces") == 1, f"{mode}: {reason}")
        results["measured"] = measured
    except Exception as exc:
        record("speed test (face AI, offline)", False, exc)

    # The launcher's "Back up data", then unlocking it again as setup does.
    backups = Path(os.environ["TEMP"]) / "securetap-check-backup"
    unlocked = Path(os.environ["TEMP"]) / "securetap-check-unlocked"
    backups.mkdir(exist_ok=True)
    unlocked.mkdir(exist_ok=True)
    code, output = manage("backup_data", str(backups), stdin=PASSWORD + "\n")
    made = sorted(backups.glob("*.securetap-backup"))
    if code == 0 and made:
        code, output = manage("open_backup", str(made[0]), str(unlocked), stdin=PASSWORD + "\n")
    record("backup made and unlocked", code == 0 and made and (unlocked / "backend" / "db.sqlite3").exists(),
           output.splitlines()[-1] if output else "")

    check = subprocess.run(
        [PYTHON, "-c", "import config, camera, camera_select, ui, student_display, offline_queue, main; "
                       "c = config.load_config(); offline_queue.OfflineQueue(c.offline_db_path, None); "
                       "d = camera_select.classify(camera.list_available_cameras()); "
                       "print(c.offline_db_path, '| cameras:', d, '| would use:', camera_select.choose(d))"],
        cwd=APP / "entry-agent", capture_output=True, text=True)
    record("gate monitor code loads", check.returncode == 0, (check.stdout or check.stderr).strip()[-300:])
    check = subprocess.run([PYTHON, "-c", "import launcher, setup_wizard; print('ok')"], cwd=APP,
                           capture_output=True, text=True)
    record("launcher and setup window load", check.returncode == 0, (check.stdout or check.stderr).strip()[-300:])

    new_files = sorted(program_files() - before)
    record("nothing written into the program folder", not new_files, new_files[:10])
    record("shortcut on the desktop", any(Path(os.environ["USERPROFILE"], "Desktop").glob("EVSU SecureTap*.lnk")))


try:
    main()
except Exception:
    record("check script itself", False, traceback.format_exc())
finally:
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
