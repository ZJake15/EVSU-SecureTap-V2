"""A short fingerprint of the backend's code, so the launcher can tell a
backend that was started before the code changed (an update installed, or a
file edited) from a current one - both answer with the same API version, and
a stale one quietly serves old code (missing pages answer 404).

The launcher computes the same thing over the same folder (launcher.py's
code_fingerprint) and compares it with what /api/health reports. Keep the
two in step: every .py file's path, size and save time, skipping folders
that hold data rather than code.
"""

import hashlib
import os
from pathlib import Path

SKIP_FOLDERS = {"__pycache__", "media", "staticfiles", "node_modules", ".git"}


def code_fingerprint(folder):
    folder = Path(folder)
    digest = hashlib.sha1()
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_FOLDERS)
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            path = Path(root) / name
            try:
                stat = path.stat()
            except OSError:
                continue
            digest.update(f"{path.relative_to(folder).as_posix()}|{stat.st_size}|{int(stat.st_mtime)}\n".encode())
    return digest.hexdigest()[:16]
