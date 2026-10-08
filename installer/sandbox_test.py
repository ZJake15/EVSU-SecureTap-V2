"""Tests the installer on a brand-new Windows: opens Windows Sandbox (a
throwaway Windows that's wiped when closed) with its internet switched OFF,
installs installer/output/EVSU-SecureTap-Setup.exe silently there and runs
installer/sandbox/check_install.py, then prints its results.

    .venv\\Scripts\\python.exe installer\\sandbox_test.py

Needs Windows Sandbox turned on (Windows 10/11 Pro: "Turn Windows features
on or off" -> Windows Sandbox, then restart). The Sandbox window stays open
afterwards so the installed SecureTap can be tried by hand; closing it
throws everything away.
"""

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

INSTALLER = Path(__file__).resolve().parent
SETUP = INSTALLER / "output" / "EVSU-SecureTap-Setup.exe"
RESULTS = INSTALLER / "build" / "sandbox-results"
CONFIG = INSTALLER / "build" / "securetap-test.wsb"

RUN_CHECK = r"""@echo off
C:\setup\EVSU-SecureTap-Setup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /TASKS=desktopicon
"%LOCALAPPDATA%\Programs\EVSU SecureTap\python\python.exe" C:\tools\check_install.py > C:\results\log.txt 2>&1
echo done > C:\results\finished.txt
"""


def main():
    sandbox = shutil.which("WindowsSandbox.exe")
    if not sandbox:
        sys.exit("Windows Sandbox isn't turned on (Turn Windows features on or off -> Windows Sandbox, restart).")
    if not SETUP.exists():
        sys.exit(f"{SETUP} doesn't exist yet - run installer/build.py first.")
    if RESULTS.exists():
        shutil.rmtree(RESULTS)
    RESULTS.mkdir(parents=True)
    tools = INSTALLER / "build" / "sandbox-tools"
    tools.mkdir(parents=True, exist_ok=True)
    shutil.copy2(INSTALLER / "sandbox" / "check_install.py", tools / "check_install.py")
    (tools / "run_check.cmd").write_text(RUN_CHECK, encoding="ascii")
    CONFIG.write_text(f"""<Configuration>
  <Networking>Disable</Networking>
  <MappedFolders>
    <MappedFolder><HostFolder>{SETUP.parent}</HostFolder><SandboxFolder>C:\\setup</SandboxFolder><ReadOnly>true</ReadOnly></MappedFolder>
    <MappedFolder><HostFolder>{tools}</HostFolder><SandboxFolder>C:\\tools</SandboxFolder><ReadOnly>true</ReadOnly></MappedFolder>
    <MappedFolder><HostFolder>{RESULTS}</HostFolder><SandboxFolder>C:\\results</SandboxFolder><ReadOnly>false</ReadOnly></MappedFolder>
  </MappedFolders>
  <LogonCommand><Command>C:\\tools\\run_check.cmd</Command></LogonCommand>
</Configuration>
""", encoding="utf-8")
    subprocess.Popen([sandbox, str(CONFIG)])
    print("Windows Sandbox is starting - installing and checking inside it (a few minutes)...", flush=True)
    deadline = time.monotonic() + 25 * 60
    while not (RESULTS / "finished.txt").exists():
        if time.monotonic() > deadline:
            sys.exit("No result after 25 minutes - look at the Sandbox window.")
        time.sleep(5)
    results = json.loads((RESULTS / "results.json").read_text(encoding="utf-8"))
    for name, outcome in results.items():
        if isinstance(outcome, dict) and "ok" in outcome:
            print(("PASS " if outcome["ok"] else "FAIL ") + name + (f" - {outcome['detail']}" if outcome["detail"]
                                                                     else ""))
    failed = [name for name, outcome in results.items() if isinstance(outcome, dict) and not outcome.get("ok", True)]
    print(f"\n{len(failed)} failed" if failed else "\nAll checks passed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
