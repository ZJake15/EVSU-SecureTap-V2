"""Builds EVSU-SecureTap-Setup.exe - the one file a new computer needs.

    .venv\\Scripts\\python.exe installer\\build.py           # full build
    .venv\\Scripts\\python.exe installer\\build.py --quick   # reuse the last build's Python + libraries

Puts together, in installer/build/SecureTap/:
  python/      a private copy of Python 3.14 (python-build-standalone, the
               portable build uv uses - with tkinter, for the windows) with
               every library already installed, MySQL support included so an
               older MySQL copy's data can be imported
  backend/, entry-agent/, launcher.py, setup_wizard.py, device_setup.py
               ONLY the files tracked in git - so no database, .env, photos,
               training data or trained model can ever end up in it
  dashboard/dist/   the dashboard, freshly built (no Node.js on the target)
  face-models/      the two face models the system uses (15 MB - no internet
                    needed on first use)
  securetap-installed.txt   the marker that makes it keep its data in
               %LOCALAPPDATA%\\EVSU SecureTap (device_setup.py)
then checks that the bundled Python can load everything, and compiles
installer/SecureTap.iss with Inno Setup into installer/output/.

Needs on the building computer: uv, Node.js (npm), Inno Setup 6, internet
(the first time), and the face models in %USERPROFILE%\\.insightface (any
earlier run of SecureTap downloaded them).
"""

import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INSTALLER = REPO / "installer"
BUILD = INSTALLER / "build"
APP = BUILD / "SecureTap"
CACHE = BUILD / "cache"
OUTPUT = INSTALLER / "output"

PYTHON_VERSION = "3.14"
# The program's own files, by git - see the module docstring.
CODE = ["launcher.py", "setup_wizard.py", "device_setup.py", "backend", "entry-agent"]
REQUIREMENTS = ["backend/requirements.txt", "entry-agent/requirements.txt", "backend/requirements-mysql.txt"]
# insightface_utils loads only these two from the buffalo_s pack
# (allowed_modules): the face finder and the fingerprint model.
FACE_MODEL_PACK = "buffalo_s"
FACE_MODEL_FILES = ("det_500m.onnx", "w600k_mbf.onnx")
# Microsoft's C++ runtime, which ONNX Runtime and OpenCV need and a fresh
# Windows may not have. Microsoft allows shipping these files next to the
# program ("app-local" deployment), so no separate installer is needed.
VC_RUNTIME = ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
              "concrt140.dll")
# Imported by the check after building - one per library the system needs.
IMPORT_CHECK = ("django, rest_framework, rest_framework_simplejwt, django_filters, corsheaders, environ, dotenv, "
                "whitenoise, insightface, onnxruntime, cv2, numpy, PIL, pillow_heif, pandas, openpyxl, bcrypt, "
                "sklearn, joblib, MySQLdb, customtkinter, requests, pygrabber, tkinter")


def step(message):
    print(f"\n== {message}", flush=True)


def run(args, **kwargs):
    print("   $ " + " ".join(str(arg) for arg in args), flush=True)
    subprocess.run([str(arg) for arg in args], check=True, **kwargs)


def app_version():
    for line in (REPO / "entry-agent" / ".env.example").read_text(encoding="utf-8").splitlines():
        if line.startswith("APP_VERSION="):
            return line.split("=", 1)[1].strip().lstrip("v")
    return "1.0"


def find_iscc():
    candidates = [Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe"]
    candidates += [Path(os.environ[name]) / "Inno Setup 6" / "ISCC.exe"
                   for name in ("ProgramFiles(x86)", "ProgramFiles") if os.environ.get(name)]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    found = shutil.which("ISCC")
    if not found:
        sys.exit("Inno Setup 6 isn't installed: winget install --id JRSoftware.InnoSetup -e --scope user")
    return Path(found)


def fresh_app_folder(keep_python):
    step("Starting from an empty build folder" + (" (keeping its Python)" if keep_python else ""))
    if APP.exists():
        assert APP.resolve().is_relative_to(BUILD.resolve())
        for child in APP.iterdir():
            if keep_python and child.name == "python":
                continue
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    APP.mkdir(parents=True, exist_ok=True)


def python_runtime():
    step(f"Private Python {PYTHON_VERSION}")
    cache = CACHE / "python"
    if not list(cache.glob("cpython-*/python.exe")):
        run(["uv", "python", "install", PYTHON_VERSION, "--install-dir", cache, "--no-bin"])
    source = max(cache.glob("cpython-*/python.exe")).parent
    shutil.copytree(source, APP / "python")
    # uv marks its Pythons as managed by it, which makes pip refuse to
    # install into them - this copy is SecureTap's own, so it goes.
    for marker in (APP / "python").rglob("EXTERNALLY-MANAGED"):
        marker.unlink()
    python = APP / "python" / "python.exe"
    if subprocess.run([python, "-m", "pip", "--version"], capture_output=True).returncode != 0:
        run([python, "-m", "ensurepip"])

    step("Libraries (the exact versions this computer runs)")
    # Pinned to what's installed here - the versions the system was tested
    # with - not whatever is newest today: requirements.txt only gives
    # lower bounds, and a newer major version (InsightFace 2.x, say) could
    # change how faces are read.
    frozen = subprocess.run([sys.executable, "-m", "pip", "freeze", "--disable-pip-version-check"],
                            capture_output=True, text=True, check=True).stdout
    pins = [line for line in frozen.splitlines() if "==" in line and not line.startswith("-e")]
    constraints = BUILD / "constraints.txt"
    constraints.write_text("\n".join(pins) + "\n", encoding="utf-8")
    print(f"   {len(pins)} versions pinned from {sys.executable}")
    args = [python, "-m", "pip", "install", "--disable-pip-version-check", "--no-warn-script-location",
            "--only-binary=:all:", "-c", constraints]
    for requirements in REQUIREMENTS:
        args += ["-r", REPO / requirements]
    run(args)

    system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    copied = []
    for name in VC_RUNTIME:
        if not (APP / "python" / name).exists() and (system32 / name).exists():
            shutil.copy2(system32 / name, APP / "python" / name)
            copied.append(name)
    print("   C++ runtime files added: " + (", ".join(copied) or "none needed"))
    return python


def program_files():
    step("Program files (git-tracked only)")
    listed = subprocess.run(["git", "ls-files", "--", *CODE], cwd=REPO, capture_output=True, text=True,
                            check=True).stdout.splitlines()
    for relative in listed:
        source = REPO / relative
        if source.is_file():  # skips files deleted in the working copy
            target = APP / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    print(f"   {len(listed)} files")


def dashboard():
    step("Dashboard (fresh build)")
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        sys.exit("Node.js (npm) is needed to build the dashboard.")
    if not (REPO / "dashboard" / "node_modules").exists():
        run([npm, "install"], cwd=REPO / "dashboard")
    run([npm, "run", "build"], cwd=REPO / "dashboard")
    shutil.copytree(REPO / "dashboard" / "dist", APP / "dashboard" / "dist")


def face_models():
    step("Face models")
    source = Path.home() / ".insightface" / "models" / FACE_MODEL_PACK
    target = APP / "face-models" / "models" / FACE_MODEL_PACK
    target.mkdir(parents=True)
    for name in FACE_MODEL_FILES:
        if not (source / name).exists():
            sys.exit(f"{source / name} is missing - run SecureTap once (it downloads the face models), then "
                     "build again.")
        shutil.copy2(source / name, target / name)


def marker(version):
    (APP / "securetap-installed.txt").write_text(
        f"EVSU SecureTap {version}, built {datetime.now():%Y-%m-%d %H:%M}.\n"
        "This file tells SecureTap it's an installed copy: it keeps its data in "
        "%LOCALAPPDATA%\\EVSU SecureTap, never in this folder.\n", encoding="utf-8")


def check(python):
    step("Checking the bundled Python can load everything")
    run([python, "-c", f"import {IMPORT_CHECK}; print('   all libraries load')"])
    with tempfile.TemporaryDirectory() as data:
        env = {**os.environ, "SECURETAP_DATA_DIR": data, "DJANGO_SECRET_KEY": "build-check-only",
               "ENTRY_AGENT_SERVICE_TOKEN": "build-check-only"}
        env.pop("PYTHONPATH", None)
        run([python, "manage.py", "check"], cwd=APP / "backend", env=env)
    # Listing cameras makes comtypes (used by pygrabber) write helper modules
    # for Windows' DirectShow into its own comtypes/gen folder the first time.
    # Do that here, so they ship ready-made and an installed copy never writes
    # into its program folder.
    run([python, "-c", "from camera import list_available_cameras; "
                       "print('   cameras on the build computer:', list_available_cameras())"],
        cwd=APP / "entry-agent")
    run([python, "-m", "compileall", "-q", APP / "backend", APP / "entry-agent", APP / "launcher.py",
         APP / "setup_wizard.py", APP / "device_setup.py"])


def size_mb(path):
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1024 ** 2


def installer(version):
    step("Installer (Inno Setup)")
    OUTPUT.mkdir(exist_ok=True)
    run([find_iscc(), f"/DAppVersion={version}", f"/DSourceDir={APP}", f"/O{OUTPUT}",
         INSTALLER / "SecureTap.iss"])
    setup = OUTPUT / "EVSU-SecureTap-Setup.exe"
    print(f"\nDone: {setup} ({setup.stat().st_size / 1024 ** 2:.0f} MB; installs {size_mb(APP):.0f} MB)")


def main():
    if not Path(sys.prefix, "pyvenv.cfg").exists():
        sys.exit("Run this with the project's venv (.venv\\Scripts\\python.exe installer\\build.py) - the "
                 "installer takes its library versions from there.")
    version = app_version()
    # --quick: reuse the last build's Python and libraries (minutes saved
    # when only SecureTap's own code changed).
    quick = "--quick" in sys.argv[1:] and (APP / "python" / "python.exe").exists()
    fresh_app_folder(keep_python=quick)
    python = APP / "python" / "python.exe" if quick else python_runtime()
    program_files()
    dashboard()
    face_models()
    marker(version)
    check(python)
    installer(version)


if __name__ == "__main__":
    main()
